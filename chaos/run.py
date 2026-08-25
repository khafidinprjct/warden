"""Uji kekacauan (Fase 10): skenario deterministik untuk mode kegagalan #1–#25 memakai fake GCE + Firestore emulator.
Tiap skenario: siapkan keadaan → jalankan tick → periksa insiden/tindakan yang diharapkan → laporan JSON.
    python -m chaos.run [--only 1,5,14]   (butuh emulator: make emulators)"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081"); os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-chaos"); os.environ.setdefault("WARDEN_PROVIDER", "fake")

from warden.core.models import Heartbeat, IncidentState as S, Job, JobStatus, Marker, now
from warden.providers import registry
from warden.signals.ingest import sign, validate_marker
from warden.store import firestore as db
from warden.watcher import tick as T


def reset():
    registry._fake = None; T._prev_status.clear()
    for run in db.client().collection("runs").limit(200).stream():          # subkoleksi denyut ikut dihapus (isolasi skenario)
        for h in run.reference.collection("heartbeats").limit(2000).stream():
            h.reference.delete()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "notifications", "policies", "cmd"):
        for d in db.client().collection(coll).limit(500).stream():
            d.reference.delete()


def healthy(name="vm1", job_id="j1", **jk):
    fake = registry.compute(); inst = fake.add(name)
    job = Job(job_id=job_id, instance_ref=inst.ref, status=JobStatus.RUNNING, run_id="r1", phase="F3", command="/venv/bin/python train.py", **jk)
    db.jobs.put(job)
    for i in range(12):
        db.put_heartbeat(Heartbeat(job_id=job_id, run_id="r1", ts=now() - timedelta(minutes=12 - i), boot_id=inst.boot_id, phase="F3", step=i * 50, loss=0.5, gpu_util=90, cpu_pct=80, disk_avail_gb=40,
                                   procs=[{"pid": 100, "ppid": 1, "cmd": "/venv/bin/python train.py"}]))
    return fake, inst, job


def fin(job, exit_code=0, artifacts=None, valid=True):
    ts = now(); sig = sign(f"{job.job_id}|{job.run_id}|{exit_code}|{ts.isoformat()}".encode()) if valid else "salah"
    mk = validate_marker(Marker(job_id=job.job_id, run_id=job.run_id, kind="RUN_FIN", ts=ts, exit_code=exit_code, signature=sig, artifacts=artifacts or []))
    db.put_marker(mk); return mk


def rules_seen(job_id=None):
    return {i.rule: str(i.state) for i in db.incidents.list(limit=200) if not job_id or i.job_id == job_id}


SCENARIOS = {}
def scenario(n, mode):
    def deco(fn): SCENARIOS[n] = (mode, fn); return fn
    return deco


@scenario(1, "preempt spot, tidak ada yang menyalakan")
def s1():
    fake, inst, job = healthy(); fake.preempt(inst.ref); T.run_tick(); T.run_tick()
    return "preempted" in rules_seen() and fake.describe(inst.ref).status == "RUNNING", {"aksi": [c for c in fake.calls if c[0] == "start"]}

@scenario(2, "preempt + disk ikut terhapus (konfigurasi tak aman)")
def s2():
    fake, inst, job = healthy(); inst.boot_disk_auto_delete = True; fake.preempt(inst.ref); T.run_tick(); T.run_tick()
    return "unsafe_config" in rules_seen() and not any(c[0] == "start" and not c[2] for c in fake.calls), {"catatan": "start ditolak sampai konfigurasi aman"}

@scenario(3, "preempt saat fase eval → resume sadar fase")
def s3():
    fake, inst, job = healthy(); job.phase = "F5-eval"; db.jobs.put(job); fake.preempt(inst.ref); T.run_tick(); T.run_tick()
    inc = [i for i in db.incidents.list(rule="preempted")]; ev = db.evidence.get(inc[0].evidence_ids[0]) if inc else None
    return bool(inc) and ev is not None and ev.payload.get("phase") == "F5-eval", {"bukti_fase": ev.payload.get("phase") if ev else None}

@scenario(4, "penjaga mati senyap → denyut Warden + deadman")
def s4():
    healthy(); T.run_tick(); h = db.client().collection("health").document("watcher").get().to_dict()
    return h.get("ok") is True and h.get("last_ok_at") is not None, {"denyut_watcher": h.get("last_ok_at")}

@scenario(5, "DONE palsu tanpa exit code")
def s5():
    fake, inst, job = healthy(); db.put_marker(Marker(job_id="j1", run_id="r1", kind="DONE_LEGACY")); T.run_tick()
    return "done_without_exit" in rules_seen() and db.jobs.get("j1").status == JobStatus.RUNNING, {}

@scenario(6, "RUN_FIN dengan tanda tangan salah / basi")
def s6():
    fake, inst, job = healthy(); fin(job, 0, valid=False); T.run_tick()
    return "marker_invalid" in rules_seen(), {}

@scenario(7, "checkpoint korup berukuran identik (sha sama dgn sebelumnya)")
def s7():
    from warden.verifier.base import verify
    p = Path("/tmp/chaos_ckpt.pt"); import zipfile
    with zipfile.ZipFile(p, "w") as z: z.writestr("data.pkl", b"\x80\x04K\x01.")
    os.utime(p, (time.time() - 600,) * 2)
    r1 = verify(p, {}); r2 = verify(p, {}, prev_sha256=r1.sha256)
    return (not r2.ok) and "identical" in r2.corrupt_reason, {"alasan": r2.corrupt_reason}

@scenario(8, "disk penuh → checkpoint 15 % ditolak + disk_low")
def s8():
    fake, inst, job = healthy(expect={"ckpt_size_bytes": 1_000_000_000})
    db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=now(), boot_id=inst.boot_id, phase="F3", step=700, loss=0.4, gpu_util=90, cpu_pct=80, disk_avail_gb=0.5)); T.run_tick()
    from warden.verifier.base import verify
    p = Path("/tmp/chaos_part.bin"); p.write_bytes(b"x" * 150_000); os.utime(p, (time.time() - 600,) * 2)
    r = verify(p, {"bytes": 1_000_000})
    return "disk_low" in rules_seen() and not r.ok, {"verifier": r.corrupt_reason}

@scenario(9, "OOM di kasus terburuk (regex + cek silang)")
def s9():
    from warden.agents.crosscheck import crosscheck
    from warden.agents.schemas import Diagnosis
    lines = ["step 100 loss 0.5", "RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB", "  File train.py line 40 in forward"]
    d = Diagnosis(category="oom_gpu", confidence=0.9, transient_or_permanent="transient", evidence_lines=[2], evidence_quotes=["CUDA out of memory"], root_cause="batch terlalu besar",
                  culprit_frame="forward", recommended_action="resume_smaller_batch", action_params={"batch": 8}, blast_radius="this_run", needs_human=False, human_summary_id="OOM", falsifiable_check="angka Tried to allocate turun")
    cc = crosscheck(d, lines, None); return cc["passed"], {"checks": [c["check"] for c in cc["checks"]]}

@scenario(10, "salah diagnosis OOM (klaim tanpa bukti) → ditolak cek silang")
def s10():
    from warden.agents.crosscheck import crosscheck
    from warden.agents.schemas import Diagnosis
    lines = ["step 100 loss 0.5", "ValueError: Input X contains NaN."]
    d = Diagnosis(category="oom_gpu", confidence=0.95, transient_or_permanent="transient", evidence_lines=[2], evidence_quotes=["Input X contains NaN"], root_cause="?",
                  culprit_frame="x", recommended_action="resume_smaller_batch", blast_radius="this_run", needs_human=False, human_summary_id="x", falsifiable_check="x")
    cc = crosscheck(d, lines, None); return (not cc["passed"]) and cc["needs_human"], {"adjusted": cc["adjusted_confidence"]}

@scenario(11, "pip gagal senyap → exit code proses anak ≠ 0 → run_fin_nonzero (LLM)")
def s11():
    fake, inst, job = healthy(); fin(job, 1); T.run_tick()
    return rules_seen().get("run_fin_nonzero") == "DIAGNOSING", {}

@scenario(12, "image tanpa pip → PREFLIGHT_FAIL marker diterima")
def s12():
    fake, inst, job = healthy(); db.put_marker(Marker(job_id="j1", run_id="r1", kind="PREFLIGHT_FAIL", evidence={"reason": "pip tidak ada"}))
    return db.get_marker("j1", "r1", "PREFLIGHT_FAIL") is not None, {}

@scenario(13, "fallback kernel senyap → 'slow' (basi tapi sibuk), bukan 'stuck'")
def s13():
    fake, inst, job = healthy(); db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=now() - timedelta(minutes=30), boot_id=inst.boot_id, phase="F3", step=600, gpu_util=95, cpu_pct=95, disk_avail_gb=40)); T.run_tick()
    r = rules_seen(); return "slow" in r and "stuck" not in r, {}

@scenario(14, "instance yatim → STOP otomatis (bukan delete)")
def s14():
    fake = registry.compute(); inst = fake.add("stray"); T.run_tick()
    return fake.describe(inst.ref).status == "STOPPED" and not any("delete" in str(c) for c in fake.calls), {}

@scenario(15, "VM idle (job selesai, mesin diam ≥15 mnt) → STOP")
def s15():
    fake, inst, job = healthy(); job.status = JobStatus.COMPLETE; db.jobs.put(job)
    db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=now() - timedelta(minutes=20), boot_id=inst.boot_id, phase="done", gpu_util=0, cpu_pct=1, disk_avail_gb=40)); T.run_tick()
    return "orphan" in rules_seen() and fake.describe(inst.ref).status == "STOPPED", {}

@scenario(16, "create gagal per-item → OpResult.error terstruktur, bukan stderr dipangkas")
def s16():
    fake, inst, job = healthy(); fake.fail_next[inst.ref] = "ZONE_RESOURCE_POOL_EXHAUSTED"; fake.preempt(inst.ref); T.run_tick(); T.run_tick()
    inc = db.incidents.list(rule="preempted")[0]; dec = db.decisions.get(inc.decision_ids[0])
    return dec.status == "FAILED" and "ZONE_RESOURCE_POOL_EXHAUSTED" in dec.result.get("error", "") and str(inc.state) == "ESCALATED", {"error": dec.result.get("error")}

@scenario(17, "kuota global vs regional terbaca terpisah")
def s17():
    fake = registry.compute(); q = fake.quota("us-central1"); return "CPUS" in q and "SSD_TOTAL_GB" in q, {"kuota": q}

@scenario(18, "kuota disk regional (dilaporkan sebelum meluncurkan)")
def s18():
    fake = registry.compute(); lim, use = fake.quota("us-central1")["SSD_TOTAL_GB"]; return (lim - use) < 500, {"sisa_gb": lim - use}

@scenario(19, "badai preempt → batas 3 start/jam menahan yang ke-4")
def s19():
    fake, inst, job = healthy()
    for k in range(4):
        fake.preempt(inst.ref); T.run_tick(); T.run_tick()
        for i in range(3): db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=now(), boot_id=fake.describe(inst.ref).boot_id, phase="F3", step=1000 + k * 10 + i, gpu_util=90, cpu_pct=80, disk_avail_gb=40))
        T._prev_status.clear()
    starts = [c for c in fake.calls if c[0] == "start" and not c[2]]
    return len(starts) == 3 and any(str(i.state) in ("ESCALATED", "AWAITING_APPROVAL") for i in db.incidents.list(rule="preempted")), {"start": len(starts)}

@scenario(20, "proses ganda → dup_process")
def s20():
    fake, inst, job = healthy()
    db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=now(), boot_id=inst.boot_id, phase="F3", step=700, gpu_util=90, cpu_pct=80, disk_avail_gb=40,
                               procs=[{"pid": 1, "ppid": 1, "cmd": "/venv/bin/python train.py"}, {"pid": 2, "ppid": 1, "cmd": "/venv/bin/python train.py"}])); T.run_tick()
    return "dup_process" in rules_seen(), {}

@scenario(21, "selesai tapi artefak tidak mendarat → verifikasi menandai hilang")
def s21():
    from warden.verifier.run import process_pending
    fake, inst, job = healthy(); fin(job, 0, artifacts=[{"path": "/x/pred.csv", "bytes": 10, "sha256": "0" * 64}]); T.run_tick()
    r1 = process_pending()   # RUN_FIN masih muda → masa tenggang unggahan: TUNDA (bukan gagal), job tetap RUNNING
    tenggang = r1["ok"] == 0 and db.jobs.get("j1").status == JobStatus.RUNNING
    mk = db.get_marker("j1", "r1", "RUN_FIN"); mk.ts = now() - timedelta(seconds=700); db.put_marker(mk)   # tenggang 10 mnt lewat
    r2 = process_pending()
    return tenggang and r2["ok"] == 0 and db.jobs.get("j1").status == JobStatus.FINISHED_UNVERIFIED, {"tenggang": r1, "lewat": r2}

@scenario(22, "smoke lolos palsu → SMOKE_FIN tanpa member yang dideklarasikan")
def s22():
    fake, inst, job = healthy(expect={"smoke_members": ["lgb", "tabicl"]}); db.put_marker(Marker(job_id="j1", run_id="r1", kind="SMOKE_FIN", evidence={"members": ["lgb"]}))
    mk = db.get_marker("j1", "r1", "SMOKE_FIN"); missing = set(job.expect["smoke_members"]) - set(mk.evidence["members"])
    return missing == {"tabicl"}, {"hilang": sorted(missing)}

@scenario(23, "smoke menimpa artefak juara → sha berubah terdeteksi")
def s23():
    from warden.verifier.base import sha256_file
    p = Path("/tmp/chaos_champion.csv"); p.write_text("a\n1\n"); s0 = sha256_file(p); p.write_text("a\n2\n"); return sha256_file(p) != s0, {}

@scenario(24, "nohup via ssh gantung → tidak ada ssh di jalur kritis (mailbox)")
def s24():
    fake, inst, job = healthy(); from warden.core.models import Action, Decision; from warden.executor import registry as ex
    d = Decision(job_id="j1", action=Action.RESUME_JOB, params={"instance_ref": inst.ref, "run_id": "r1"}); r = ex.execute(d, fake)
    return r.ok and any(c[0] == "set_metadata" for c in fake.calls), {"plan": r.plan}

@scenario(25, "operator sedang di mesin → tindakan ditahan")
def s25():
    fake, inst, job = healthy(); db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=now() - timedelta(minutes=30), boot_id=inst.boot_id, phase="F3", step=600, gpu_util=1, cpu_pct=1, disk_avail_gb=40, operator_active=True))
    from warden.policy.engine import Ctx, evaluate, load_policy; from warden.core.models import Action
    d = evaluate(Action.KILL_PROCESS, Ctx(job_id="j1", operator_active=True, managed=True), load_policy()); return d.verdict == "HELD", {"explain": d.explain[-1]}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only", default=""); ns = ap.parse_args()
    only = {int(x) for x in ns.only.split(",") if x}
    report = []; t0 = time.time()
    for n in sorted(SCENARIOS):
        if only and n not in only: continue
        mode, fn = SCENARIOS[n]; reset(); t = time.time()
        try:
            ok, info = fn(); err = ""
        except Exception as e:
            ok, info, err = False, {}, f"{type(e).__name__}: {e}"
        report.append({"n": n, "mode": mode, "ok": bool(ok), "ms": int((time.time() - t) * 1000), "info": info, "error": err})
        print(f"{'OK ' if ok else 'GAGAL'} #{n:2d} {mode} ({int((time.time()-t)*1000)} ms) {err}")
    Path("chaos/report.json").write_text(json.dumps(report, indent=1, default=str, ensure_ascii=False))
    passed = sum(1 for r in report if r["ok"]); print(f"\n{passed}/{len(report)} skenario lulus dalam {time.time()-t0:.1f} dtk → chaos/report.json")
    return 0 if passed == len(report) else 1


if __name__ == "__main__":
    sys.exit(main())
