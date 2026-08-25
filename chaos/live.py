"""Uji kekacauan HIDUP di GCP (Fase 10 gerbang hidup). Urutan:
  1. tunggu job demo COMPLETE (RUN_FIN → verifier → VERIFIED)          → Fase 5 hidup
  2. kirim resume (run baru), tunggu fase F3 berjalan
  3. simulate-maintenance-event (fallback stop) → tunggu insiden preempted/stopped_external RESOLVED + mesin RUNNING → Fase 2/3 hidup
  4. tunggu run baru COMPLETE (resume sadar 'belum ada RUN_FIN')
Laporan JSON di chaos/live_report.json. Jalankan: python -m chaos.live"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from warden.store import firestore as db

JOB = os.environ.get("WARDEN_DEMO_JOB", "climate-demo"); REF = "us-central1-a/demo-train-1"; ZONE, NAME = REF.split("/")
P = open(".gcp_project").read().strip(); G = "/home/ubuntu/google-cloud-sdk/bin/gcloud"
log: list[dict] = []


def note(step: str, ok: bool, **info):
    log.append({"ts": datetime.now(timezone.utc).isoformat(), "step": step, "ok": ok, **info}); print(("OK   " if ok else "GAGAL"), step, json.dumps(info, default=str)[:300], flush=True)


def wait(pred, label: str, timeout_s: int, every: int = 30):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            r = pred()
        except Exception as e:
            r = None; print("  (tunggu) error:", str(e)[:80], flush=True)
        if r:
            note(label, True, seconds=int(time.time() - t0), detail=r); return r
        time.sleep(every)
    note(label, False, seconds=int(time.time() - t0)); return None


def job_status():
    j = db.jobs.get(JOB); return str(j.status), j.run_id, j.phase, j.last_step


def main() -> int:
    # 1. COMPLETE run saat ini
    r = wait(lambda: (lambda s: s if s[0] == "COMPLETE" else None)(job_status()), "1 job COMPLETE (RUN_FIN → VERIFIED)", 1500)
    if not r:
        incs = [(i.rule, str(i.state), i.summary[:120]) for i in db.incidents.list(job_id=JOB, limit=20)]
        note("1 diagnosa", False, incidents=incs); return finish(1)
    ver = db.get_marker(JOB, r[1], "VERIFIED"); note("1b VERIFIED marker", ver is not None, artifacts=(ver.artifacts if ver else None))
    # 2. resume → run baru
    j = db.jobs.get(JOB); j.status = "RUNNING"; j.run_id = ""; db.jobs.put(j)
    db.client().collection("cmd").document(JOB).set({"cmd": "resume", "args": {}, "by": "chaos.live"})
    r2 = wait(lambda: (lambda s: s if s[0] == "RUNNING" and s[2].startswith("F3") and s[1] else None)(job_status()), "2 run baru mencapai F3", 900)
    if not r2:
        return finish(1)
    run2 = r2[1]
    # 3. preempt nyata
    out = subprocess.run([G, "compute", "instances", "simulate-maintenance-event", NAME, "--zone", ZONE, "--project", P, "--quiet"], capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        note("3 simulate-maintenance-event", False, err=out.stderr[-200:])
        out = subprocess.run([G, "compute", "instances", "stop", NAME, "--zone", ZONE, "--project", P, "--quiet"], capture_output=True, text=True, timeout=300)
        note("3 fallback stop", out.returncode == 0, err=out.stderr[-120:])
    else:
        note("3 simulate-maintenance-event", True)
    t_preempt = time.time()
    inc = wait(lambda: next(((i.rule, str(i.state)) for i in db.incidents.list(job_id=JOB, limit=50) if i.rule in ("preempted", "stopped_external") and i.created_at.timestamp() > t_preempt - 5), None),
               "3b insiden preempt terdeteksi", 900, every=20)
    res = wait(lambda: next(((i.rule, str(i.state)) for i in db.incidents.list(job_id=JOB, limit=50) if i.rule in ("preempted", "stopped_external") and i.created_at.timestamp() > t_preempt - 5 and str(i.state) == "RESOLVED"), None),
               "3c insiden RESOLVED (start otomatis)", 900, every=20)
    st = subprocess.run([G, "compute", "instances", "describe", NAME, "--zone", ZONE, "--project", P, "--format=value(status)"], capture_output=True, text=True).stdout.strip()
    note("3d mesin RUNNING lagi", st == "RUNNING", status=st, kerugian_detik=int(time.time() - t_preempt))
    # 4. resume sadar 'belum ada RUN_FIN' → run lanjut sampai COMPLETE
    r4 = wait(lambda: (lambda s: s if s[0] == "COMPLETE" and s[1] != run2 else None)(job_status()), "4 run pasca-preempt COMPLETE", 1800)
    return finish(0 if r4 else 1)


def finish(code: int) -> int:
    json.dump(log, open("chaos/live_report.json", "w"), indent=1, default=str)
    print(f"\n{sum(1 for x in log if x['ok'])}/{len(log)} langkah OK → chaos/live_report.json", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
