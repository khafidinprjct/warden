"""LIVE gate for checklist A4 — phase-aware resume across a REAL preemption during the eval phase.

Drill #5 proved a resume out of a failed *training* run. A4 stayed partial because the other half was never tested live:
what happens when the machine is taken away while the job is in eval/export, after training has already finished.

This drill uses a real Spot preemption (`gcloud compute instances simulate-maintenance-event`, the documented way to
preempt a Spot VM on purpose), not a stop, so the same GCE preemption operation the `preempted` rule matches on is produced.

What must be true for the gate to pass:
  1. the job reaches phase "eval" with training complete (step == steps);
  2. a real preemption happens *inside* that phase — verified against zoneOperations, not assumed;
  3. Warden detects `preempted` and starts the machine again by itself (start_instance is L2 — no human);
  4. the resumed run re-enters the eval phase from its start and does NOT re-run training (the step never goes backwards);
  5. the job reaches COMPLETE with every artifact opened and VERIFIED (eval.jsonl 10 rows, pred.csv 2000 rows);
  6. the close-out rule stops the machine.

Cost: one e2-medium Spot VM for < 30 min (≈ $0.01). Deterministic path — no Gemini call is required for `preempted`.
    python -m chaos.live_phase_resume [--keep]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from chaos.live_lifecycle import (B, CORE, FS, G, LATEST, P, REPORT, STAMP, incidents, instance, job,
                                  last_hb, log, post, wait)

STEPS = 400
EVAL_SLEEP = 15          # 10 folds × 15 s = a 150 s window to be preempted inside the eval phase
JOB_ID = f"live-{STAMP}-phase"


def spec(job_id: str) -> dict:
    args = f"--eval-sleep {EVAL_SLEEP}"
    cmd = (f"bash -c 'gcloud storage cp gs://{B}/demo/toy_bootstrap.sh /opt/toy_bootstrap.sh -q && "
           f"TOY_STEPS={STEPS} TOY_SLEEP=0.15 TOY_ARGS=\"{args}\" bash /opt/toy_bootstrap.sh'")
    return {"job_id": job_id, "command": cmd, "machine_type": "e2-medium",
            "zones": ["us-central1-b", "us-central1-c"],          # zone a had a Spot preempt storm on 25–26 Aug
            "spot": True, "disk_gb": 20,
            # the jsonl verifier checks min_rows, not rows — "rows" here would be accepted and silently never checked
            "expect": {"pred.csv": {"rows": 2000}, "eval.jsonl": {"min_rows": 10}, "steps": STEPS},
            "budget_cap_usd": 0.5, "entry": "toy_train.py", "labels": {"warden-role": "live-test"}}


def gcloud(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([G, *args, "--project", P], capture_output=True, text=True)


def preempt_operations(ref: str) -> list[dict]:
    """The real preemption events GCE recorded for this VM (the evidence catalogue #31 taught us to read properly)."""
    zone, name = ref.split("/", 1)
    r = gcloud("compute", "operations", "list", "--filter", f'operationType=compute.instances.preempted AND zone:{zone}',
               "--format", "json(targetLink,operationType,insertTime,status)")
    try:
        ops = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        log("could not read zoneOperations", stderr=r.stderr[:200]); return []
    return [o for o in ops if str(o.get("targetLink", "")).endswith("/" + name)]


def artifact_rows(name: str) -> int | None:
    r = gcloud("storage", "cat", f"gs://{B}/jobs/{JOB_ID}/artifacts/{name}")
    return None if r.returncode else len([l for l in r.stdout.splitlines() if l.strip()])


def run() -> dict:
    verdict: dict = {"job": JOB_ID, "gate": "A4", "checks": {}}
    log(f"A4 GATE: preemption inside the eval phase — job {JOB_ID}, {STEPS} steps, {EVAL_SLEEP}s per eval fold")
    r = post("/jobs/launch", json.dumps(spec(JOB_ID)).encode(), spec(JOB_ID))
    assert r.get("ok"), r
    log("launched", zone=r.get("zone"), instance=r.get("instance_ref"))

    assert wait("job RUNNING on first heartbeat", lambda: job(JOB_ID).get("status") == "RUNNING", 600)
    ref = job(JOB_ID)["instance_ref"]

    # 1. training must finish and the job must actually be inside the eval phase
    hb = wait("phase = eval with training finished (step == steps)",
              lambda: (lambda h: h if h.get("phase") == "eval" and (h.get("step") or 0) >= STEPS else None)(last_hb(JOB_ID)), 900, 10)
    assert hb, "job never reached the eval phase"
    step_before, run_before = hb.get("step"), hb.get("run_id")
    verdict["checks"]["reached_eval"] = {"step": step_before, "run_id": run_before}

    # 2. a REAL preemption, inside that phase
    zone, name = ref.split("/", 1)
    log("triggering a real Spot preemption inside the eval phase", instance=ref)
    pr = gcloud("compute", "instances", "simulate-maintenance-event", name, "--zone", zone)
    log("simulate-maintenance-event", rc=pr.returncode, err=pr.stderr.strip()[:200])
    assert pr.returncode == 0, pr.stderr
    ops = wait("GCE recorded a real preemption operation for this VM", lambda: preempt_operations(ref) or None, 420, 15)
    still_eval = last_hb(JOB_ID).get("phase")
    verdict["checks"]["real_preemption"] = {"operations": len(ops or []), "phase_at_preempt": still_eval}
    assert ops, "no preemption operation — the drill did not test what it claims"

    # 3. Warden reacts on its own
    inc = wait("incident `preempted` opened", lambda: next((i for i in incidents(JOB_ID) if i["rule"] == "preempted"), None), 600, 15)
    assert inc, f"no preempted incident; rules saw: {sorted({i['rule'] for i in incidents(JOB_ID)})}"
    dec = wait("start_instance executed automatically (L2, no human)",
               lambda: next((d for d in [FS.collection("decisions").document(x).get().to_dict()
                                         for x in next((i for i in incidents(JOB_ID) if i["rule"] == "preempted"), {}).get("decision_ids", [])]
                             if d and d["action"] == "start_instance" and d["status"] in ("DONE", "EXECUTING")), None), 600, 15)
    assert dec, "Warden did not start the machine by itself"
    verdict["checks"]["auto_restart"] = {"action": dec["action"], "verdict": dec["verdict"], "status": dec["status"]}
    assert wait("machine RUNNING again", lambda: instance(ref).get("status") == "RUNNING", 600, 15)

    # 4. the eval phase re-runs from its start, and training is NOT re-run
    low = {"step": step_before}
    def _back_in_eval():
        h = last_hb(JOB_ID)
        low["step"] = min(low["step"], h.get("step") or low["step"])
        return h if h.get("phase") in ("eval", "export") and h.get("run_id") != run_before else None
    hb2 = wait("resumed run is back in the eval phase (new run id)", _back_in_eval, 900, 10)
    assert hb2, "the machine came back but the job never re-entered eval"
    verdict["checks"]["phase_resumed"] = {"phase": hb2.get("phase"), "step": hb2.get("step"),
                                          "run_before": run_before, "run_after": hb2.get("run_id"),
                                          "lowest_step_seen_after_restart": low["step"]}
    assert low["step"] >= STEPS, f"training was re-run: step fell to {low['step']} (< {STEPS})"

    # 5. complete and verified
    assert wait("job COMPLETE (artifacts opened & VERIFIED)", lambda: job(JOB_ID).get("status") == "COMPLETE", 1200, 20)
    rows = {"eval.jsonl": artifact_rows("eval.jsonl"), "pred.csv": artifact_rows("pred.csv")}
    verdict["checks"]["artifacts"] = rows
    assert rows["eval.jsonl"] == 10, f"eval.jsonl has {rows['eval.jsonl']} rows — the eval phase did not finish cleanly"
    assert rows["pred.csv"] == 2001, f"pred.csv has {rows['pred.csv']} rows (2000 + header)"
    rep = wait("final report written", lambda: job(JOB_ID).get("report") or None, 400, 15)
    verdict["checks"]["report"] = {k: rep.get(k) for k in ("spent_usd", "ettr", "incidents")} if rep else None

    # 6. close-out
    assert wait("machine stopped by the close-out rule",
                lambda: instance(ref).get("status") in ("TERMINATED", "STOPPING", "STOPPED"), 600, 15)
    verdict["pass"] = True
    return verdict


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--keep", action="store_true", help="keep the VM and its disk after a pass")
    ns = ap.parse_args()
    import urllib.request
    for _ in range(40):        # catalogue #30: the previous revision can still be serving minutes after "deployed"
        try:
            rev = json.loads(urllib.request.urlopen(CORE + "/health", timeout=20).read()).get("revision", "")
        except Exception:      # noqa: BLE001
            rev = ""
        if rev in (LATEST, "local"):
            break
        log("core not ready" if rev else "health unreadable — not ready", serving=rev, want=LATEST); time.sleep(15)
    red = {d.id: (d.to_dict() or {}).get("last_error", "")[:80] for d in FS.collection("health").stream() if (d.to_dict() or {}).get("ok") is False}
    log("health rows RED before the drill (catalogue #33: a red row invalidates the drill)", red=red or "none")
    assert not red, f"refusing to run with red health rows: {red}"

    out: dict = {"job": JOB_ID, "pass": False}
    try:
        out = run(); log("A4 GATE PASSED")
    except Exception as e:      # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"; log(f"A4 GATE FAILED: {out['error']}")
    finally:
        ref = job(JOB_ID).get("instance_ref", "")
        if ref:
            zone, name = ref.split("/", 1)
            gcloud("compute", "instances", "stop", name, "--zone", zone, "--quiet")
            log("machine STOPPED")
            if out.get("pass") and not ns.keep:       # a passing drill leaves no bill behind; a failing one keeps the evidence
                gcloud("compute", "instances", "delete", name, "--zone", zone, "--quiet")
                gcloud("compute", "disks", "delete", name, "--zone", zone, "--quiet")
                log("machine and disk deleted (drill passed; evidence is in the report)")
        out["events"] = REPORT
        Path("chaos/live_phase_resume_report.json").write_text(json.dumps(out, indent=1, default=str))
        print("report → chaos/live_phase_resume_report.json")
        raise SystemExit(0 if out.get("pass") else 1)
