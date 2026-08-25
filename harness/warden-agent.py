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
    if not CORE:
        return None
    req = urllib.request.Request(f"{CORE}/cmd/{JOB}", headers={"X-Warden-Signature": sig(JOB.encode())})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log(f"GET cmd gagal: {e}")
        return None


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


_uploaded: set[str] = set()


def upload_log() -> None:
    if not BUCKET:
        return
    logp = os.path.join(DIR, "run.log")
    if os.path.exists(logp):
        subprocess.run(["bash", "-c", f"tail -c 262144 '{logp}' | gcloud storage cp - gs://{BUCKET}/jobs/{JOB}/log/tail.log -q"],
                       capture_output=True, timeout=60)
    # artefak: unggah berkas ≤200 MB yang mtime-nya sudah diam ≥ 60 dtk (ukur saat penulis diam)
    adir = os.path.join(DIR, "artifacts")
    if os.path.isdir(adir):
        for name in os.listdir(adir):
            fp = os.path.join(adir, name)
            if not os.path.isfile(fp) or name.endswith((".tmp", ".partial")) or fp in _uploaded:
                continue
            fin_ada = os.path.exists(os.path.join(DIR, "markers", "RUN_FIN.json"))
            if os.path.getsize(fp) > 200 * 1024 * 1024 or (time.time() - os.path.getmtime(fp) < 60 and not fin_ada):
                continue            # setelah RUN_FIN semua artefak final → unggah segera
            r = subprocess.run(["gcloud", "storage", "cp", fp, f"gs://{BUCKET}/jobs/{JOB}/artifacts/{name}", "-q"], capture_output=True, timeout=300)
            if r.returncode == 0:
                _uploaded.add(fp)


def handle_cmd(c: dict) -> None:
    cmd, args = c.get("cmd"), c.get("args", {})
    log(f"perintah: {cmd} {args}")
    if cmd == "kill":
        for p in host_stats()["procs"]:
            if p["ppid"] == 1 or not args.get("pid") or p["pid"] == args.get("pid"):
                os.kill(p["pid"], signal.SIGTERM)
    elif cmd == "resume":
        wd = os.environ.get("WARDEN_WORKDIR", "/"); os.makedirs(wd, exist_ok=True)
        subprocess.Popen(["bash", "-c", os.environ.get("WARDEN_RESUME_CMD", "true")], cwd=wd,
                         stdout=open("/var/log/warden-resume.log", "a"), stderr=subprocess.STDOUT)
    elif cmd == "quarantine":
        p = args.get("path", "")
        if p and os.path.exists(p):
            q = os.path.join(DIR, "quarantine"); os.makedirs(q, exist_ok=True)
            os.replace(p, os.path.join(q, os.path.basename(p)))
    elif cmd == "collect_diag":
        with open(os.path.join(DIR, "diag.txt"), "w") as f:
            f.write(sh(["dmesg", "--time-format", "iso"], 20)[-20000:])
    elif cmd == "inject":                      # hanya untuk latihan/demo (Fase 10); aman: hanya menyentuh DIR job
        what = args.get("what")
        if what == "corrupt_csv":
            for p in os.listdir(os.path.join(DIR, "artifacts")):
                fp = os.path.join(DIR, "artifacts", p)
                if p.endswith(".csv"):
                    lines = open(fp).read().splitlines()
                    keep = lines[: max(2, int(len(lines) * 0.6))]
                    keep[1] = keep[1].rsplit(",", 1)[0] + ",nan"
                    open(fp, "w").write("\n".join(keep) + "\n")


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
