#!/usr/bin/env python3
"""warden-agent — daemon di mesin (systemd, stdlib saja). Tiap 30 detik:
  1. kirim denyut host (cpu/gpu/df/proses/log mtime/operator/preempt) + train.json terbaru → POST /ingest/heartbeat (HMAC)
  2. kirim marker baru (RUN_FIN dsb.) → POST /ingest/marker
  3. unggah potongan log ke GCS (gcloud storage cp, bila ada) — jalur pasif untuk Warden
  4. poll mailbox GET /cmd/<job> → perintah allowlist (kill/resume/quarantine/rollback/verify)
  5. pantau tanda preempt (metadata server) → SIGUSR1 ke proses job (flush checkpoint darurat)
Denyut dikirim juga saat semuanya baik (P4)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

CORE = os.environ.get("WARDEN_CORE_URL", "").rstrip("/")
SECRET = os.environ.get("WARDEN_HMAC", "")
JOB = os.environ.get("WARDEN_JOB", "")
DIR = os.path.join(os.environ.get("WARDEN_DIR", "/var/lib/warden"), JOB)
BUCKET = os.environ.get("WARDEN_BUCKET", "")
INTERVAL = int(os.environ.get("WARDEN_INTERVAL", "30"))
ENTRY = os.environ.get("WARDEN_ENTRY", "")          # substring path penuh entrypoint job (untuk hitung proses ganda)
_sent_markers: set[str] = set()


def sig(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def post(path: str, payload: dict) -> bool:
    if not CORE:
        return False
    body = json.dumps(payload).encode()
    req = urllib.request.Request(CORE + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Warden-Signature": sig(body)})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status < 300
    except Exception as e:
        log(f"POST {path} gagal: {e}")
        return False


def get_cmd() -> dict | None:
    """Poll the mailbox. A command is executed only if its signature (HMAC over cmd/args/decision_id/ts/nonce) is valid:
    a document written into Firestore by anything other than warden-core is ignored and reported."""
    if not CORE:
        return None
    req = urllib.request.Request(f"{CORE}/cmd/{JOB}", headers={"X-Warden-Signature": sig(JOB.encode())})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            c = json.loads(r.read().decode())
    except Exception as e:
        log(f"GET cmd gagal: {e}")
        return None
    if not c or not c.get("cmd"):
        return None
    canon = json.dumps({k: c.get(k) for k in ("cmd", "args", "decision_id", "ts", "nonce")}, sort_keys=True, separators=(",", ":")).encode()
    if not hmac.compare_digest(sig(canon), c.get("sig", "")):
        log(f"perintah {c.get('cmd')} DITOLAK: tanda tangan tidak sah")
        post_result(c, False, "signature invalid — command rejected by harness")
        return None
    return c


def post_result(c: dict, ok: bool, detail: str = "", **extra) -> None:
    post("/ingest/cmd_result", {"job_id": JOB, "cmd": c.get("cmd"), "nonce": c.get("nonce", ""), "decision_id": c.get("decision_id", ""),
                                "ok": bool(ok), "detail": str(detail)[:400], "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **extra})


def log(msg: str) -> None:
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(DIR, "agent.log"), "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")


def sh(cmd: list[str], timeout: int = 10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def host_stats() -> dict:
    d: dict = {}
    try:
        with open("/proc/loadavg") as f:
            load1 = float(f.read().split()[0])
        d["cpu_pct"] = round(min(100.0, 100.0 * load1 / max(os.cpu_count() or 1, 1)), 1)
    except Exception:
        pass
    try:
        st = os.statvfs(DIR if os.path.exists(DIR) else "/")
        d["disk_avail_gb"] = round(st.f_bavail * st.f_frsize / 1e9, 2)
    except Exception:
        pass
    out = sh(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"])
    if out.strip():
        try:
            u, m, t = [float(x) for x in out.strip().splitlines()[0].split(",")]
            d.update(gpu_util=u, vram_used_mb=m, vram_total_mb=t)
        except Exception:
            pass
    procs = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode(errors="ignore").strip()
            if ENTRY and ENTRY in cmd:
                with open(f"/proc/{pid}/stat") as f:
                    ppid = int(f.read().split(")")[-1].split()[1])
                procs.append({"pid": int(pid), "ppid": ppid, "cmd": cmd[:200]})
        except Exception:
            continue
    d["procs"] = procs
    logp = os.path.join(DIR, "run.log")
    if os.path.exists(logp):
        d["log_mtime"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(logp)))
    d["operator_active"] = bool(sh(["who"]).strip())
    d["open_writers"] = [p for p in os.listdir(os.path.join(DIR, "artifacts")) if p.endswith((".tmp", ".partial"))] if os.path.isdir(os.path.join(DIR, "artifacts")) else []
    try:
        req = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/preempted", headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=2) as r:
            d["preempt_notice"] = r.read().decode().strip() == "TRUE"
    except Exception:
        d["preempt_notice"] = False
    try:
        d["boot_id"] = open("/proc/sys/kernel/random/boot_id").read().strip()
    except Exception:
        d["boot_id"] = ""
    return d


import re
_RX_PHASE = re.compile(r"=== \[(F[0-9A-Za-z-]+)\]")
_RX_STEP = re.compile(r"(?:step|iter)[ =:]+(\d+)", re.I)
_RX_LOSS = re.compile(r"loss[ =:]+(nan|inf|[0-9]*\.?[0-9]+)", re.I)
_RX_LGB = re.compile(r"^\[(\d+)\]\s+valid_0's \w+: ([0-9.]+|nan|inf)")   # baris LightGBM: [121] valid_0's binary_logloss: 0.49


def train_stats() -> dict:
    """Utama: train.json dari warden.beat(). Cadangan (mode legacy): parse ekor run.log →
    denyut sintetis (phase dari '=== [Fx] ===', step/loss dari pola umum), ditandai synthetic=True."""
    p = os.path.join(DIR, "train.json"); rs = os.path.join(DIR, "markers", "RUN_START.json")
    if os.path.exists(p):
        try:
            t = json.load(open(p))
            # train.json milik run LAMA bila RUN_START.json lebih baru → jangan kirim step/loss basi atas nama run baru
            if os.path.exists(rs) and os.path.getmtime(rs) > os.path.getmtime(p):
                try: rid = json.load(open(rs)).get("run_id", "")
                except Exception: rid = ""
                if rid and rid != t.get("run_id"):
                    return {"run_id": rid, "phase": t.get("phase")}
            return {k: t.get(k) for k in ("run_id", "phase", "step", "epoch", "loss", "lr", "grad_norm", "step_per_s")}
        except Exception:
            return {}
    logp = os.path.join(DIR, "run.log")
    if not os.path.exists(logp):
        return {}
    try:
        with open(logp, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 65536))
            tail = f.read().decode(errors="ignore").splitlines()
    except Exception:
        return {}
    out: dict = {"synthetic": True}
    for line in tail:
        m = _RX_PHASE.search(line)
        if m: out["phase"] = m.group(1)
        m = _RX_LGB.match(line)
        if m:
            out["step"] = int(m.group(1)); out["loss"] = float(m.group(2)) if m.group(2) not in ("nan", "inf") else float(m.group(2))
            continue
        m = _RX_STEP.search(line)
        if m: out["step"] = int(m.group(1))
        m = _RX_LOSS.search(line)
        if m: out["loss"] = float(m.group(1))
    rs = os.path.join(DIR, "markers", "RUN_START.json")
    if os.path.exists(rs):
        try: out["run_id"] = json.load(open(rs)).get("run_id", "")
        except Exception: pass
    return out


def send_markers() -> None:
    mdir = os.path.join(DIR, "markers")
    if not os.path.isdir(mdir):
        return
    for name in sorted(os.listdir(mdir)):
        if not name.endswith(".json"):
            continue
        try:
            mk = json.load(open(os.path.join(mdir, name)))
        except Exception:
            continue
        key = f"{name}:{mk.get('run_id','')}:{int(os.path.getmtime(os.path.join(mdir, name)))}"   # RUN_FIN run baru menimpa nama yang sama → kunci = nama+run+mtime
        if key in _sent_markers:
            continue
        payload = {"job_id": JOB, "run_id": mk.get("run_id", ""), "kind": mk.get("kind", name.replace(".json", "")),
                   "ts": mk.get("ts") if isinstance(mk.get("ts"), str) else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mk.get("ts", time.time()))),
                   "exit_code": mk.get("exit_code"), "signal": mk.get("signal"), "phase": mk.get("phase", ""),
                   "boot_id": mk.get("boot_id", ""), "artifacts": mk.get("artifacts", []), "evidence": mk.get("evidence", {}),
                   "signature": mk.get("signature", "")}
        if post("/ingest/marker", payload):
            _sent_markers.add(key)


_sync = {"running": False, "last": 0.0, "fin_mtime": 0.0}


def _rsync_artifacts(adir: str) -> None:
    """Satu proses rsync (bukan cp per file) di thread latar: insiden 25 Agu — 135×cp ≈ 10 mnt menyumbat loop denyut setelah boot."""
    try:
        subprocess.run(["gcloud", "storage", "rsync", adir, f"gs://{BUCKET}/jobs/{JOB}/artifacts", "-q",
                        "-x", r".*\.(tmp|partial|corrupt)$|.*\.corrupt\..*"], capture_output=True, timeout=900)
    except Exception as e:  # noqa: BLE001
        log(f"rsync artefak gagal: {e}")
    finally:
        _sync["running"] = False; _sync["last"] = time.time()


def upload_log() -> None:
    if not BUCKET:
        return
    logp = os.path.join(DIR, "run.log")
    if os.path.exists(logp):
        subprocess.run(["bash", "-c", f"tail -c 262144 '{logp}' | gcloud storage cp - gs://{BUCKET}/jobs/{JOB}/log/tail.log -q"],
                       capture_output=True, timeout=60)
        # per-run copy so the log of a failed run survives the next run (evidence for the investigator)
        rs = os.path.join(DIR, "markers", "RUN_START.json")
        try:
            rid = json.load(open(rs)).get("run_id", "") if os.path.exists(rs) else ""
        except Exception:
            rid = ""
        if rid:
            subprocess.run(["bash", "-c", f"tail -c 262144 '{logp}' | gcloud storage cp - gs://{BUCKET}/jobs/{JOB}/log/{rid}.log -q"], capture_output=True, timeout=60)
    # artefak: rsync di thread latar — segera setelah RUN_FIN baru, selain itu paling cepat tiap 5 mnt; loop denyut tidak menunggu
    adir = os.path.join(DIR, "artifacts"); finp = os.path.join(DIR, "markers", "RUN_FIN.json")
    if not os.path.isdir(adir) or _sync["running"]:
        return
    fin_m = os.path.getmtime(finp) if os.path.exists(finp) else 0.0
    if fin_m > _sync["fin_mtime"] or time.time() - _sync["last"] > 300:
        _sync["running"] = True; _sync["fin_mtime"] = fin_m
        import threading
        threading.Thread(target=_rsync_artifacts, args=(adir,), daemon=True).start()


def _launch_resume(env: dict | None = None, reason: str = "") -> str:
    """Re-run the job's resume command (wrun) with extra environment. Returns the pid of the shell."""
    wd = os.environ.get("WARDEN_WORKDIR", "/"); os.makedirs(wd, exist_ok=True)
    cmd = os.environ.get("WARDEN_RESUME_CMD", "")
    if not cmd:
        raise RuntimeError("WARDEN_RESUME_CMD not set on this machine")
    e = dict(os.environ); e.update({k: str(v) for k, v in (env or {}).items()})
    e["WARDEN_HMAC"] = SECRET; e["WARDEN_BUCKET"] = BUCKET
    p = subprocess.Popen(["bash", "-c", cmd], cwd=wd, env=e, stdout=open("/var/log/warden-resume.log", "a"), stderr=subprocess.STDOUT, start_new_session=True)
    log(f"resume diluncurkan pid={p.pid} env={ {k: v for k, v in (env or {}).items()} } alasan={reason}")
    return str(p.pid)


def _entry_pids() -> list[dict]:
    return [p for p in host_stats()["procs"]]


def _kill_entry(pid: int | None = None, grace_s: int = 20) -> list[int]:
    """SIGTERM the entrypoint process(es) (whole process group), SIGKILL after grace. Returns pids killed."""
    targets = [p["pid"] for p in _entry_pids() if not pid or p["pid"] == pid]
    for t in targets:
        try:
            os.killpg(os.getpgid(t), signal.SIGTERM)
        except Exception:
            try: os.kill(t, signal.SIGTERM)
            except Exception: pass
    deadline = time.time() + grace_s
    while time.time() < deadline and any(os.path.exists(f"/proc/{t}") for t in targets):
        time.sleep(1)
    for t in targets:
        if os.path.exists(f"/proc/{t}"):
            try: os.killpg(os.getpgid(t), signal.SIGKILL)
            except Exception:
                try: os.kill(t, signal.SIGKILL)
                except Exception: pass
    return targets


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _gcs_md5(name: str) -> str:
    """md5 of the object in Storage (base64 in gcloud output → hex)."""
    if not BUCKET:
        return ""
    out = sh(["gcloud", "storage", "objects", "describe", f"gs://{BUCKET}/jobs/{JOB}/artifacts/{name}", "--format=value(md5_hash)"], 60).strip()
    if not out:
        return ""
    import base64
    try:
        return base64.b64decode(out).hex()
    except Exception:
        return ""


def _clean_disk(keep: int = 2) -> tuple[int, list[str], list[str]]:
    """Delete local checkpoint FILES older than the newest `keep` whose md5 equals the copy in Storage. Never touches anything else."""
    adir = os.path.join(DIR, "artifacts")
    if not os.path.isdir(adir):
        return 0, [], ["no artifacts dir"]
    cks = sorted([p for p in os.listdir(adir) if p.startswith("ckpt_") and not p.endswith((".tmp", ".corrupt", ".sha256", ".json", ".rolledback"))])
    freed = 0; removed: list[str] = []; skipped: list[str] = []
    for name in cks[:-keep] if keep > 0 else cks:
        fp = os.path.join(adir, name)
        if not os.path.isfile(fp):
            continue
        remote = _gcs_md5(name)
        if not remote or remote != _md5(fp):
            skipped.append(f"{name}: no verified copy in Storage"); continue
        size = os.path.getsize(fp)
        os.remove(fp); freed += size; removed.append(name)
        for side in (".meta.json", ".sha256", ".meta.json.sha256"):
            if os.path.exists(fp + side):
                os.remove(fp + side)
    return freed, removed, skipped


def _rollback(ckpt: str, back: int = 1) -> tuple[str, list[str]]:
    """Set aside checkpoints newer than the target (rename → .rolledback; nothing deleted) so the trainer resumes from the target."""
    adir = os.path.join(DIR, "artifacts")
    cks = sorted([p for p in os.listdir(adir) if p.startswith("ckpt_") and p.endswith(".npz") or (p.startswith("ckpt_") and p.endswith((".pt", ".pth", ".ckpt")))]) if os.path.isdir(adir) else []
    target = os.path.basename(ckpt) if ckpt else ""
    if target not in cks:
        # no explicit target: go `back` checkpoints from the newest
        if len(cks) <= back:
            raise RuntimeError(f"cannot roll back {back}: only {len(cks)} checkpoints")
        target = cks[-1 - back]
    moved: list[str] = []
    for name in cks[cks.index(target) + 1:]:
        fp = os.path.join(adir, name); os.replace(fp, fp + ".rolledback"); moved.append(name)
        for side in (".meta.json", ".sha256"):
            if os.path.exists(fp + side):
                os.replace(fp + side, fp + ".rolledback" + side)
    return target, moved


def handle_cmd(c: dict) -> None:
    cmd, args = c.get("cmd"), c.get("args", {}) or {}
    log(f"perintah: {cmd} {args}")
    try:
        if cmd == "kill":
            killed = _kill_entry(args.get("pid"))
            if args.get("then_resume"):
                pid = _launch_resume(args.get("env"), "kill+resume")
                post_result(c, True, f"killed {killed}; resumed pid {pid}", killed=killed)
            else:
                post_result(c, True, f"killed {killed}", killed=killed)
        elif cmd == "resume":
            if _entry_pids():
                _kill_entry()
            if args.get("clean"):
                adir = os.path.join(DIR, "artifacts")
                if os.path.isdir(adir):
                    os.replace(adir, adir + ".prev-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime()))   # archived, not deleted
                os.makedirs(adir, exist_ok=True)
                for m in ("RUN_FIN.json", "RUN_START.json"):
                    try: os.remove(os.path.join(DIR, "markers", m))
                    except FileNotFoundError: pass
            pid = _launch_resume(args.get("env"), args.get("reason", ""))
            post_result(c, True, f"resume launched pid {pid} mode={args.get('mode')}", pid=pid)
        elif cmd == "rollback":
            if _entry_pids():
                _kill_entry()
            target, moved = _rollback(args.get("ckpt", ""), int(args.get("back", 1)))
            env = dict(args.get("env") or {}); env["WARDEN_RESUME_CKPT"] = target
            pid = _launch_resume(env, args.get("reason", ""))
            post_result(c, True, f"rolled back to {target}; set aside {moved}; resumed pid {pid}", target=target, moved=moved)
        elif cmd == "quarantine":
            p = args.get("path", "")
            if p and not os.path.isabs(p):
                p = os.path.join(DIR, "artifacts", p)
            if p and os.path.exists(p):
                q = os.path.join(DIR, "quarantine"); os.makedirs(q, exist_ok=True)
                os.replace(p, os.path.join(q, os.path.basename(p)))
                post_result(c, True, f"quarantined {p}")
            else:
                post_result(c, False, f"path not found: {p}")
        elif cmd == "clean_disk":
            freed, removed, skipped = _clean_disk(int(args.get("keep", 2)))
            post_result(c, True, f"freed {freed} bytes: {removed}; skipped {skipped[:5]}", freed_bytes=freed, removed=removed)
        elif cmd == "grow_fs":
            out = sh(["bash", "-c", "growpart /dev/sda 1 2>&1; resize2fs /dev/sda1 2>&1 || xfs_growfs / 2>&1; df -h / | tail -1"], 120)
            post_result(c, True, out[-300:])
        elif cmd == "collect_diag":
            with open(os.path.join(DIR, "diag.txt"), "w") as f:
                f.write(sh(["dmesg", "--time-format", "iso"], 20)[-20000:])
            post_result(c, True, "diag collected")
        elif cmd == "inject":                      # drills only (Phase 10); safe: touches only the job dir
            what = args.get("what")
            if what == "corrupt_csv":
                for p in os.listdir(os.path.join(DIR, "artifacts")):
                    fp = os.path.join(DIR, "artifacts", p)
                    if p.endswith(".csv"):
                        lines = open(fp).read().splitlines()
                        keep = lines[: max(2, int(len(lines) * 0.6))]
                        keep[1] = keep[1].rsplit(",", 1)[0] + ",nan"
                        open(fp, "w").write("\n".join(keep) + "\n")
            elif what == "fill_disk":
                fp = os.path.join(DIR, "artifacts", "ckpt_000001.npz")   # an old, unbacked-up-looking checkpoint + a big filler
                subprocess.run(["bash", "-c", f"fallocate -l {int(args.get('gb', 1))}G {DIR}/filler.bin"], timeout=60)
            post_result(c, True, f"injected {what}")
        else:
            post_result(c, False, f"unknown command {cmd}")
    except Exception as e:  # noqa: BLE001 — always report, never die silently
        log(f"perintah {cmd} GAGAL: {e}")
        post_result(c, False, f"{type(e).__name__}: {e}")


def main() -> None:
    log(f"agent mulai job={JOB} core={CORE or '-'} bucket={BUCKET or '-'}")
    preempt_signaled = False
    while True:
        try:
            h = host_stats(); t = train_stats()
            hb = {"job_id": JOB, "run_id": t.get("run_id") or "", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **h,
                  **{k: v for k, v in t.items() if k != "run_id"}}
            ok = post("/ingest/heartbeat", hb)
            send_markers(); upload_log()
            if h.get("preempt_notice") and not preempt_signaled:
                for p in h["procs"]:
                    os.kill(p["pid"], signal.SIGUSR1)       # trainer: flush checkpoint darurat ≤30 dtk
                preempt_signaled = True
                log("PREEMPT NOTICE → SIGUSR1 ke proses job")
            c = get_cmd()
            if c and c.get("cmd"):
                handle_cmd(c)
            log(f"denyut ok={ok} step={t.get('step')} loss={t.get('loss')} cpu={h.get('cpu_pct')} disk={h.get('disk_avail_gb')}")   # jejak sukses (P4)
        except Exception as e:
            log(f"error loop: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
