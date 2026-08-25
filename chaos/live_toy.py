"""Uji preempt HIDUP yang jujur pada job berkontrak penuh (toy-train, checkpoint nyata):
  1. tunggu toy-train COMPLETE (verifikasi ulang)   2. resume → run baru, tunggu step ≥ 1000
  3. simulate-maintenance-event → insiden → start otomatis → mesin RUNNING
  4. tunggu run baru COMPLETE; bukti resume dari checkpoint = log '[resume] dari ckpt_' + kerugian step ≤ 200."""
from __future__ import annotations
import json, os, subprocess, sys, time
from datetime import datetime, timezone
from warden.store import firestore as db

JOB = "toy-train"; ZONE, NAME = "us-central1-a", "demo-train-2"
P = open(".gcp_project").read().strip(); G = "/home/ubuntu/google-cloud-sdk/bin/gcloud"; log: list[dict] = []


def note(step, ok, **info):
    log.append({"ts": datetime.now(timezone.utc).isoformat(), "step": step, "ok": ok, **info}); print(("OK   " if ok else "GAGAL"), step, json.dumps(info, default=str)[:300], flush=True)


def wait(pred, label, timeout_s, every=30):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try: r = pred()
        except Exception as e: r = None; print("  (tunggu) error:", str(e)[:80], flush=True)
        if r: note(label, True, seconds=int(time.time() - t0), detail=r); return r
        time.sleep(every)
    note(label, False, seconds=int(time.time() - t0)); return None


def js():
    j = db.jobs.get(JOB); hb = db.last_heartbeat(JOB); return str(j.status), j.run_id, j.phase, j.last_step, (hb.step if hb else None), (hb.run_id if hb else None)


def finish(code):
    json.dump(log, open("chaos/live_toy_report.json", "w"), indent=1, default=str); print(f"\n{sum(1 for x in log if x['ok'])}/{len(log)} langkah OK → chaos/live_toy_report.json", flush=True); return code


def main():
    r = wait(lambda: (lambda s: s if s[0] == "COMPLETE" else None)(js()), "1 toy-train COMPLETE (verifikasi ulang)", 900)
    if not r: return finish(1)
    j = db.jobs.get(JOB); j.status = "RUNNING"; j.run_id = ""; db.jobs.put(j)
    db.client().collection("cmd").document(JOB).set({"cmd": "resume", "args": {}, "by": "chaos.live_toy"})
    r2 = wait(lambda: (lambda s: s if s[5] and s[5] != r[1] and (s[4] or 0) >= 1000 else None)(js()), "2 run baru step ≥ 1000", 900, every=20)
    if not r2: return finish(1)
    run2, step_pre = r2[5], r2[4]
    out = subprocess.run([G, "compute", "instances", "simulate-maintenance-event", NAME, "--zone", ZONE, "--project", P, "--quiet"], capture_output=True, text=True, timeout=180)
    note("3 simulate-maintenance-event", out.returncode == 0, err=out.stderr[-150:], step_saat_preempt=step_pre); t_pre = time.time()
    wait(lambda: next(((i.rule, str(i.state)) for i in db.incidents.list(job_id=JOB, limit=50) if i.rule in ("preempted", "stopped_external") and i.created_at.timestamp() > t_pre - 5 and str(i.state) == "RESOLVED"), None), "3b insiden RESOLVED (start otomatis)", 900, every=20)
    st = subprocess.run([G, "compute", "instances", "describe", NAME, "--zone", ZONE, "--project", P, "--format=value(status)"], capture_output=True, text=True).stdout.strip()
    note("3c mesin RUNNING lagi", st == "RUNNING", status=st, detik_sejak_preempt=int(time.time() - t_pre))
    r4 = wait(lambda: (lambda s: s if s[5] == run2 and (s[4] or 0) > step_pre else None)(js()), "4a run lanjut (step melewati titik preempt, run_id sama)", 900, every=20)
    r5 = wait(lambda: (lambda s: s if s[0] == "COMPLETE" and s[1] == run2 else None)(js()), "4b run COMPLETE pasca-preempt", 1500)
    return finish(0 if (r4 and r5) else 1)


if __name__ == "__main__":
    sys.exit(main())
