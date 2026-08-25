"""Checklist J4: Warden's ledger vs. Cloud Billing. Cloud Billing has no per-day API for a project; the supported source is the
BigQuery billing export. This script (a) finds an export dataset in the project, (b) sums cost per day for Compute Engine,
(c) compares with `costs/<day>.compute_usd` in Firestore and prints the deviation. Without an export it says so — it never estimates.
    python infra/billing_reconcile.py [--days 7]"""
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

P = Path(__file__).resolve().parent.parent.joinpath(".gcp_project").read_text().strip()
BQ = "/home/ubuntu/google-cloud-sdk/bin/bq"


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=7); ns = ap.parse_args()
    ds = subprocess.run([BQ, "ls", "--project_id", P, "--format=prettyjson"], capture_output=True, text=True)
    tables = []
    if ds.returncode == 0 and ds.stdout.strip():
        import json
        for d in json.loads(ds.stdout):
            name = d.get("datasetReference", {}).get("datasetId", "")
            t = subprocess.run([BQ, "ls", "--project_id", P, "--format=prettyjson", name], capture_output=True, text=True)
            if t.returncode == 0 and t.stdout.strip():
                tables += [f"{name}.{x['tableReference']['tableId']}" for x in json.loads(t.stdout) if x["tableReference"]["tableId"].startswith("gcp_billing_export")]
    if not tables:
        print("NOT CONFIGURED: no BigQuery billing export table in this project (Billing → Billing export → BigQuery). "
              "The ledger cannot be reconciled against the invoice until the export exists; nothing is estimated.")
        return 2
    table = tables[0]
    sql = (f"SELECT DATE(usage_start_time) d, ROUND(SUM(cost),4) usd FROM `{P}.{table}` WHERE service.description='Compute Engine' "
           f"AND usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {ns.days} DAY) GROUP BY d ORDER BY d")
    r = subprocess.run([BQ, "query", "--project_id", P, "--use_legacy_sql=false", "--format=prettyjson", sql], capture_output=True, text=True)
    if r.returncode != 0:
        print("query failed:", r.stderr[-300:]); return 1
    import json, os
    os.environ.setdefault("WARDEN_PROJECT", P)
    from warden.store import firestore as db
    rows = json.loads(r.stdout or "[]"); worst = 0.0
    print(f"{'day':<12}{'billing':>10}{'ledger':>10}{'dev%':>8}")
    for x in rows:
        led = db.client().collection("costs").document(x["d"]).get().to_dict() or {}
        l = float(led.get("compute_usd", 0.0)); b = float(x["usd"]); dev = (abs(l - b) / b * 100) if b else 0.0; worst = max(worst, dev)
        print(f"{x['d']:<12}{b:>10.4f}{l:>10.4f}{dev:>8.1f}")
    print("OK: within ±10 %" if worst <= 10 else f"DEVIATION {worst:.1f} % > 10 % — check machine price table (_PRICE) and tick accrual")
    return 0 if worst <= 10 else 3


if __name__ == "__main__":
    sys.exit(main())
