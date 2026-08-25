import os
os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081"); os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test"); os.environ.setdefault("WARDEN_PROVIDER", "fake")
from datetime import timedelta
from warden.core.models import Heartbeat, IncidentState, Job, JobStatus, now
from warden.executor import approvals
from warden.providers import registry
from warden.store import firestore as db
from warden.watcher import tick as T


def _reset():
    registry._fake = None; T._prev_status.clear()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "notifications", "policies"):
        for d in db.client().collection(coll).limit(300).stream():
            d.reference.delete()


def test_freeze_then_approve_flow():
    _reset()
    fake = registry.compute(); inst = fake.add("stray2")
    approvals.freeze("khaf", True)
    s = T.run_tick(); assert s["held"] == 1 and fake.describe(inst.ref).status == "RUNNING"   # dibekukan: tidak stop
    approvals.freeze("khaf", False)
    # buat yatim butuh izin: job legacy → stop turun ke L1
    job = Job(job_id="jl", instance_ref=inst.ref, status=JobStatus.COMPLETE, legacy=True); db.jobs.put(job)
    inst.job_id = "jl"
    T._prev_status.clear()
    for d in db.client().collection("incidents").limit(50).stream(): d.reference.delete()
    s = T.run_tick(); assert s["approval"] == 1
    dec = [d for d in db.decisions.list(status="PENDING") if d.verdict == "NEED_APPROVAL"][0]
    r = approvals.approve(dec.decision_id, "khaf"); assert r["ok"], r
    assert fake.describe(inst.ref).status == "STOPPED"
    inc = db.incidents.get(dec.incident_id); assert inc.state == IncidentState.RESOLVED
    assert any(a.to_dict()["actor"] == "human:khaf" for a in db.client().collection("audit").stream())
