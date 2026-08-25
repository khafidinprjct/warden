"""Checklist H5: soak measurement — over the last N days, did Warden take any action on a job that was actually healthy?
A false action = a non-notify action executed (DONE) on an incident whose job had no failed run, no preempt event and no operator
request in the same hour, and which a human later marked false positive or which verification could not confirm.
    python -m chaos.soak [--days 7]"""
from __future__ import annotations
import argparse, json, os
from datetime import timedelta
from pathlib import Path

P = Path(__file__).resolve().parent.parent.joinpath(".gcp_project").read_text().strip()
os.environ.setdefault("WARDEN_PROJECT", P)
from warden.core.models import now  # noqa: E402
from warden.store import firestore as db  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=7); ns = ap.parse_args()
since = now() - timedelta(days=ns.days)
incs = [i for i in db.incidents.list(limit=2000) if i.created_at >= since]
acted = []; false_actions = []
for i in incs:
    for did in i.decision_ids:
        d = db.decisions.get(did)
        if not d or d.action == "notify" or str(d.status) != "DONE":
            continue
        acted.append((i, d))
        if str(i.state) == "FALSE_POSITIVE" or (i.verify or {}).get("result") == "fail" and i.rule in ("idle", "orphan", "stuck", "disk_low", "disk_trend"):
            false_actions.append({"incident": i.incident_id, "job": i.job_id, "rule": i.rule, "action": str(d.action), "state": str(i.state)})
rep = {"days": ns.days, "incidents": len(incs), "actions": len(acted), "false_actions": false_actions, "resolved_by_warden": sum(1 for i in incs if str(i.state) == "RESOLVED" and i.attempt > 0),
       "needed_human": sum(1 for i in incs if str(i.state) in ("ESCALATED", "CLOSED")), "ts": now().isoformat()}
print(json.dumps(rep, indent=1))
db.client().collection("eval").document(f"soak-{now():%Y%m%d}").set(rep)
print("GATE H5:", "PASS (0 false actions)" if not false_actions else f"FAIL ({len(false_actions)} false actions)")
