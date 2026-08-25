import os
os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081"); os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test"); os.environ.setdefault("WARDEN_PROVIDER", "fake")
from datetime import timedelta
from warden.core.models import DecisionStatus, IncidentState, Job, JobStatus, now
from warden.executor import approvals
from warden.providers import registry
from warden.store import firestore as db
from warden.watcher import tick as T


def _reset():
    registry._fake = None; T._prev_status.clear()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "notifications", "policies", "policy_overrides"):
        for d in db.client().collection(coll).limit(300).stream():
            d.reference.delete()


def _pending_orphan_stop():
    fake = registry.compute(); inst = fake.add("stray3")
    job = Job(job_id="jl", instance_ref=inst.ref, status=JobStatus.COMPLETE, legacy=True); db.jobs.put(job); inst.job_id = "jl"
    s = T.run_tick(); assert s["approval"] == 1
    return fake, inst, [d for d in db.decisions.list(status="PENDING") if d.verdict == "NEED_APPROVAL"][0]


def test_expired_decision_can_be_reevaluated_and_then_approved():
    _reset()
    fake, inst, dec = _pending_orphan_stop()
    dec.expires_at = now() - timedelta(minutes=1); db.decisions.put(dec)
    assert approvals.approve(dec.decision_id, "khaf")["ok"] is False           # kedaluwarsa → ditolak
    r = approvals.reevaluate(dec.decision_id, "khaf"); assert r["ok"], r
    assert db.decisions.get(dec.decision_id).status == DecisionStatus.EXPIRED
    new = db.decisions.get(r["decision_id"]); assert new.status == DecisionStatus.PENDING and new.explain[0].startswith("re-evaluated")
    inc = db.incidents.get(dec.incident_id); assert inc.state == IncidentState.AWAITING_APPROVAL and new.decision_id in inc.decision_ids
    assert approvals.approve(new.decision_id, "khaf")["ok"]
    assert fake.describe(inst.ref).status == "STOPPED"


def test_always_approves_and_writes_override():
    _reset()
    fake, inst, dec = _pending_orphan_stop()
    r = approvals.always(dec.decision_id, "khaf"); assert r["ok"], r
    assert fake.describe(inst.ref).status == "STOPPED"
    ov = db.client().collection("policy_overrides").document(f"{dec.job_id}:{dec.action.value}").get()
    assert ov.exists and ov.to_dict()["level"] == "L2" and ov.to_dict()["until"] > now().timestamp() + 80000


def test_reevaluate_refuses_live_pending():
    _reset()
    fake, inst, dec = _pending_orphan_stop()
    assert approvals.reevaluate(dec.decision_id, "khaf")["ok"] is False
