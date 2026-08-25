"""Job lifecycle (checklist A): one spec → Warden launches the machine, guards it, harvests, verifies, closes out.

  launch(spec)   ledger first (P7) → pick zone (stock-outs, quota) → create VM with the harness metadata → job PENDING
  (harness)      first heartbeat → RUNNING (ingest) · wrun uploads artifacts + RUN_FIN → verifier opens them → VERIFIED/COMPLETE
  close_out(job) final report (cost, ETTR, artifacts, incidents) → job.report + reports/<job> · machine stopped by rule complete_running
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from warden.config import settings
from warden.core.models import AuditEntry, Job, JobStatus, now
from warden.providers.registry import compute
from warden.store import firestore as db

HARNESS_DIR = Path(__file__).resolve().parent.parent / "harness"
REQUIRED = ("job_id", "command")
DEFAULTS = {"machine_type": "e2-medium", "zones": ["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"], "spot": True,
            "disk_gb": 20, "image_family": "ubuntu-2404-lts-amd64", "image_project": "ubuntu-os-cloud", "workdir": "/opt/job",
            "entry": "", "expect": {}, "budget_cap_usd": 0.0, "env": {}, "labels": {}, "min_cpus_free": 2}


def validate(spec: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in REQUIRED if not spec.get(k)]
    if missing:
        raise ValueError(f"spec missing {missing}")
    if not str(spec["job_id"]).replace("-", "").replace("_", "").isalnum():
        raise ValueError("job_id: letters, digits, - and _ only")
    s = {**DEFAULTS, **spec}
    s["zones"] = list(s["zones"]) if isinstance(s["zones"], (list, tuple)) else [s["zones"]]
    s["name"] = s.get("name") or f"warden-{s['job_id']}"[:60]
    return s


def _metadata(s: dict[str, Any]) -> dict[str, str]:
    core = (settings.self_url or "").rstrip("/")
    bucket = settings.bucket
    md = {"warden-job": s["job_id"], "warden-core-url": core, "warden-hmac": settings.ingest_hmac_secret, "warden-bucket": bucket,
          "warden-harness-url": f"gs://{bucket}/harness", "warden-entry": s["entry"] or s["command"].split()[0],
          "warden-resume-cmd": s["command"], "warden-workdir": s["workdir"],
          "startup-script": (HARNESS_DIR / "startup.sh").read_text()}
    for k, v in (s.get("env") or {}).items():
        md[f"warden-env-{k}"] = str(v)
    return md


def pick_zone(s: dict[str, Any], tried: list[str] | None = None) -> tuple[str, list[str]]:
    """Zone choice (A2): first candidate without a recent stock-out for this machine type and with CPU quota headroom. Returns (zone, explain)."""
    ex: list[str] = []
    for z in s["zones"]:
        if tried and z in tried:
            continue
        if db.stockout_recent(z, s["machine_type"]):
            ex.append(f"{z}: skipped, stock-out in the last 30 min"); continue
        try:
            q = compute().quota(z.rsplit("-", 1)[0])
            lim, use = q.get("CPUS", (0.0, 0.0))
            if lim and (lim - use) < s["min_cpus_free"]:
                ex.append(f"{z}: skipped, CPU quota {use:.0f}/{lim:.0f}"); continue
        except Exception as e:  # noqa: BLE001 — quota is advisory
            ex.append(f"{z}: quota unknown ({str(e)[:60]})")
        ex.append(f"{z}: chosen")
        return z, ex
    return "", ex


def launch(spec: dict[str, Any], actor: str = "operator") -> dict[str, Any]:
    s = validate(spec)
    if db.jobs.get(s["job_id"]):
        raise ValueError(f"job {s['job_id']} already exists")
    # ledger BEFORE the machine (P7): an orphan is impossible even if this process dies mid-way
    job = Job(job_id=s["job_id"], name=s.get("name_human") or s["job_id"], command=s["command"], status=JobStatus.PENDING, phase="launch",
              expect=s["expect"], budget_cap_usd=float(s["budget_cap_usd"]), spec=s, zone_candidates=s["zones"],
              artifact_prefix=f"gs://{settings.bucket}/jobs/{s['job_id']}/artifacts" if settings.bucket else "")
    db.jobs.put(job)
    tried: list[str] = []; attempts: list[dict] = []; ref = ""
    while True:
        zone, why = pick_zone(s, tried)
        if not zone:
            break
        tried.append(zone)
        vm = {"name": s["name"], "zone": zone, "machine_type": s["machine_type"], "spot": s["spot"], "job_id": s["job_id"],
              "image_family": s["image_family"], "image_project": s["image_project"], "disk_gb": s["disk_gb"],
              "labels": {**s["labels"], "warden-role": s.get("role", "job")}, "metadata": _metadata(s), "gpu": s.get("gpu", ""), "gpu_count": s.get("gpu_count", 1)}
        db.audit(AuditEntry(actor=actor, phase="intent", action="launch", target=f"{zone}/{s['name']}", before={"spec": {k: v for k, v in s.items() if k != "env"}, "zone_choice": why}))
        r = compute().create(vm)
        attempts.append({"zone": zone, "ok": r.ok, "error": r.error, "plan": r.plan})
        db.audit(AuditEntry(actor=actor, phase="result", action="launch", target=f"{zone}/{s['name']}", after={"observed": r.observed, "plan": r.plan}, ok=r.ok, error=r.error))
        if r.ok:
            ref = r.observed; break
        if "ZONE_RESOURCE_POOL_EXHAUSTED" in (r.error or "") or "does not have enough resources" in (r.error or ""):
            db.stockout_mark(zone, s["machine_type"], r.error); continue
        break   # any other error is not a stock-out: stop trying zones
    job = db.jobs.get(s["job_id"])
    if ref:
        job.instance_ref = ref; job.phase = "boot"; db.jobs.put(job)
        print(json.dumps({"event": "warden.launch", "severity": "INFO", "job": s["job_id"], "instance": ref, "attempts": len(attempts)}), flush=True)
        return {"ok": True, "job_id": s["job_id"], "instance_ref": ref, "attempts": attempts, "hourly_usd": attempts[-1]["plan"].get("hourly_usd")}
    job.status = JobStatus.FAILED; job.phase = "launch_failed"; db.jobs.put(job)
    print(json.dumps({"event": "warden.launch", "severity": "ERROR", "job": s["job_id"], "attempts": attempts}, default=str), flush=True)
    return {"ok": False, "job_id": s["job_id"], "attempts": attempts, "error": (attempts[-1]["error"] if attempts else "no zone available")}


def report(job: Job) -> dict[str, Any]:
    """Final report at close-out (A6): what it cost, how effective the machine time was, what landed, what happened."""
    from warden.steward.ledger import ettr
    incs = [i for i in db.incidents.list(job_id=job.job_id, limit=500)]
    verified = db.get_marker(job.job_id, job.run_id, "VERIFIED")
    hbs = db.recent_heartbeats(job.job_id, 500)
    first = hbs[0].ts if hbs else job.last_heartbeat_at; last = hbs[-1].ts if hbs else job.last_heartbeat_at
    rep = {"job_id": job.job_id, "status": str(job.status), "instance_ref": job.instance_ref, "run_id": job.run_id,
           "started_at": first.isoformat() if first else None, "finished_at": (last or now()).isoformat(),
           "wall_h": round(((last - first).total_seconds() / 3600), 2) if first and last else None,
           "spent_usd": round(job.spent_usd, 4), "budget_cap_usd": job.budget_cap_usd,
           "ettr": ettr(job.job_id, window_hours=24 * 30).get("ettr"),
           "artifacts": [a for a in (verified.artifacts if verified else [])],
           "incidents": {"total": len(incs), "resolved_by_warden": sum(1 for i in incs if str(i.state).endswith("RESOLVED") and i.attempt > 0),
                         "needed_human": sum(1 for i in incs if str(i.state).endswith(("ESCALATED", "CLOSED")) or any(db.decisions.get(d) and db.decisions.get(d).approved_by for d in i.decision_ids)),
                         "by_rule": {}},
           "llm_usd": round(sum(i.llm_cost_usd for i in incs), 4), "written_at": now().isoformat()}
    for i in incs:
        rep["incidents"]["by_rule"][i.rule] = rep["incidents"]["by_rule"].get(i.rule, 0) + 1
    job.report = rep; db.jobs.put(job)
    db.client().collection("reports").document(job.job_id).set(rep)
    return rep
