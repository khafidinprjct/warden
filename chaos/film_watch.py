"""The terminal pane of the demo take: what the machine and the job are doing, refreshed in place.

This is the "terminal logs" half of the evidence the rules ask for, beside the dashboard's "UI changes".
Everything printed is read live from Compute Engine and Firestore — nothing is staged.

    python -m chaos.film_watch <job_id>
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("WARDEN_PROJECT", open(os.path.join(ROOT, ".gcp_project")).read().strip())
os.environ.setdefault("WARDEN_PROVIDER", "gce")

from warden.store import firestore as db  # noqa: E402

JOB = sys.argv[1] if len(sys.argv) > 1 else ""
G = "/home/ubuntu/google-cloud-sdk/bin/gcloud"
P = os.environ["WARDEN_PROJECT"]
C = {"dim": "\033[38;5;245m", "ok": "\033[38;5;41m", "warn": "\033[38;5;214m",
     "crit": "\033[38;5;203m", "hi": "\033[38;5;255m", "acc": "\033[38;5;75m", "0": "\033[0m"}


def machines() -> str:
    r = subprocess.run([G, "compute", "instances", "list", "--project", P,
                        "--format=value(name,zone,status,machineType.basename())"],
                       capture_output=True, text=True, timeout=25)
    return r.stdout.strip() or "(none)"


def main() -> None:
    while True:
        now = datetime.now(timezone.utc)
        out = [f"{C['acc']}Google Cloud · project {P}{C['0']}",
               f"{C['dim']}{now:%H:%M:%S} UTC · Cloud Run: warden-core, warden-ui, warden-deadman{C['0']}", ""]
        out.append(f"{C['hi']}COMPUTE ENGINE{C['0']}")
        for line in machines().splitlines():
            f = line.split("\t")
            col = C["ok"] if len(f) > 2 and f[2] == "RUNNING" else C["dim"]
            out.append(f"  {col}{f[0]:26s} {f[1]:16s} {f[2] if len(f)>2 else '':12s}{C['0']}")
        out.append("")

        j = db.jobs.get(JOB) if JOB else None
        hb = db.last_heartbeat(JOB) if JOB else None
        out.append(f"{C['hi']}JOB {JOB}{C['0']}")
        out.append(f"  status   {C['ok']}{j.status if j else '—'}{C['0']}")
        if hb:
            age = (now - hb.ts).total_seconds()
            out.append(f"  phase    {hb.phase}   step {C['hi']}{hb.step}{C['0']}"
                       + (f"   loss {hb.loss:.4f}" if hb.loss is not None else ""))
            out.append(f"  {C['dim']}heartbeat {age:.0f}s ago · run {hb.run_id}{C['0']}")
        out.append("")

        incs = sorted([i for i in db.incidents.list(limit=100) if not JOB or i.job_id == JOB],
                      key=lambda x: x.created_at, reverse=True)[:4]
        out.append(f"{C['hi']}INCIDENTS{C['0']}")
        if not incs:
            out.append(f"  {C['dim']}none — the job is healthy{C['0']}")
        for i in incs:
            col = C["crit"] if i.severity == "critical" else C["warn"] if i.severity == "warning" else C["dim"]
            out.append(f"  {col}{i.rule:20s}{C['0']} {str(i.state):18s} {C['dim']}{i.summary[:44]}{C['0']}")
            for did in i.decision_ids[-2:]:
                d = db.decisions.get(did)
                if d:
                    out.append(f"      {C['acc']}→ {str(d.action):22s}{C['0']} {str(d.verdict):14s} {str(d.status)}"
                               + (f"  {C['dim']}by {d.approved_by}{C['0']}" if d.approved_by else ""))
        print("\033[2J\033[H" + "\n".join(out), flush=True)
        time.sleep(3)


if __name__ == "__main__":
    main()
