"""CLI operator: python -m warden.cli <perintah> ...
  job add <job_id> --instance <zone/name> --command <entry> [--phase F0] [--expect-json '{...}'] [--legacy]
  job list | job show <id> | tick | steward | freeze on|off | approve <decision_id> | deny <decision_id> | ettr <job_id>"""
from __future__ import annotations

import argparse
import json
import sys

from warden.core.models import Job, JobStatus
from warden.store import firestore as db


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="warden")
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("job"); js = j.add_subparsers(dest="jcmd", required=True)
    a = js.add_parser("add"); a.add_argument("job_id"); a.add_argument("--instance", required=True); a.add_argument("--command", required=True)
    a.add_argument("--phase", default=""); a.add_argument("--expect-json", default="{}"); a.add_argument("--legacy", action="store_true"); a.add_argument("--budget", type=float, default=0.0)
    js.add_parser("list"); s = js.add_parser("show"); s.add_argument("job_id")
    sub.add_parser("tick"); sub.add_parser("steward"); f = sub.add_parser("freeze"); f.add_argument("state", choices=["on", "off"])
    ap_ = sub.add_parser("approve"); ap_.add_argument("decision_id"); dn = sub.add_parser("deny"); dn.add_argument("decision_id")
    e = sub.add_parser("ettr"); e.add_argument("job_id")
    l = sub.add_parser("launch", help="one job spec (json/yaml) → Warden creates the machine and guards the job"); l.add_argument("spec")
    ns = ap.parse_args(argv)
    if ns.cmd == "launch":
        import yaml
        from warden import lifecycle
        spec = yaml.safe_load(open(ns.spec)); r = lifecycle.launch(spec, actor="cli"); print(json.dumps(r, indent=1, default=str)); return 0 if r.get("ok") else 2
    if ns.cmd == "job" and ns.jcmd == "add":
        job = Job(job_id=ns.job_id, name=ns.job_id, instance_ref=ns.instance, command=ns.command, phase=ns.phase,
                  status=JobStatus.RUNNING, expect=json.loads(ns.expect_json), legacy=ns.legacy, budget_cap_usd=ns.budget)
        db.jobs.put(job); print("job ditulis:", job.model_dump_json()[:300]); return 0
    if ns.cmd == "job" and ns.jcmd == "list":
        for x in db.jobs.list(): print(x.job_id, x.status, x.instance_ref, x.phase, x.last_step, f"${x.spent_usd:.3f}")
        return 0
    if ns.cmd == "job" and ns.jcmd == "show":
        x = db.jobs.get(ns.job_id); print(x.model_dump_json(indent=1) if x else "tidak ada"); return 0
    if ns.cmd == "tick":
        from warden.watcher.tick import run_tick; print(json.dumps(run_tick(), indent=1)); return 0
    if ns.cmd == "steward":
        from warden.steward import ledger; print(json.dumps({"accrue": ledger.accrue(600), "projection": ledger.projection()}, indent=1)); return 0
    if ns.cmd == "freeze":
        from warden.executor.approvals import freeze; print(freeze("cli", ns.state == "on")); return 0
    if ns.cmd in ("approve", "deny"):
        from warden.executor import approvals; print(getattr(approvals, ns.cmd)(ns.decision_id, "cli")); return 0
    if ns.cmd == "ettr":
        from warden.steward.ledger import ettr; print(ettr(ns.job_id)); return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
