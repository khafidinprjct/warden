"""Checklist B (trend patrol) and G (graduated trust): rules fire before an incident; approvals promote, failed verification demotes."""
import os
import pytest
from datetime import timedelta

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")

from warden.core.models import Action, Decision, DecisionStatus, Heartbeat, Incident, IncidentState as S, Instance, InstanceStatus, Job, JobStatus, Verdict, now
from warden.policy.engine import load_policy
from warden.providers import registry
from warden.steward import ledger
from warden.store import firestore as db
from warden.watcher import rules as R, tick as T


@pytest.fixture(autouse=True)
def fresh():
    registry._fake = None; T._prev_status.clear()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "cmd", "cmd_results", "stockouts", "postmortems", "costs", "policies", "policy_overrides"):
        for d in db.client().collection(coll).limit(300).stream():
            if coll == "runs":
                for h in d.reference.collection("heartbeats").limit(500).stream():
                    h.reference.delete()
            d.reference.delete()
    yield


def _facts(hbs, expect=None):
    inst = Instance(ref="us-central1-a/vm", name="vm", zone="us-central1-a", status=InstanceStatus.RUNNING, managed=True, boot_id="b1", hourly_price_usd=0.01)
    job = Job(job_id="j1", instance_ref=inst.ref, status=JobStatus.RUNNING, run_id="r1", phase="train", command="/venv/bin/python x.py", expect=expect or {})
    return R.Facts(t=now(), inst=inst, job=job, hb=hbs[-1], hbs=hbs, policy=load_policy(), boot_age_min=60)


def _hbs(n=30, **f):
    out = []
    for i in range(n):
        kw = {k: (v(i) if callable(v) else v) for k, v in f.items()}
        out.append(Heartbeat(job_id="j1", run_id="r1", ts=now() - timedelta(minutes=(n - i) * 5), boot_id="b1", phase="train", step=i * 100, **kw))
    return out


def test_throughput_drop_needs_busy_machine():
    hbs = _hbs(step_per_s=lambda i: 10.0 if i < 25 else 4.0, gpu_util=90, cpu_pct=50, loss=lambda i: 1 / (i + 1))
    f = [x for x in R.evaluate(_facts(hbs)) if x.rule == "throughput_drop"]
    assert f and f[0].needs_llm and "−60%" in f[0].summary
    hbs = _hbs(step_per_s=lambda i: 10.0 if i < 25 else 4.0, gpu_util=2, cpu_pct=5, loss=lambda i: 1 / (i + 1))
    assert not [x for x in R.evaluate(_facts(hbs)) if x.rule == "throughput_drop"]     # idle machine: that is 'stuck', not a slowdown


def test_grad_spike_and_plateau_and_vram_creep():
    hbs = _hbs(grad_norm=lambda i: 1.0 if i < 29 else 40.0, loss=0.5, gpu_util=90, cpu_pct=50)
    assert any(x.rule == "grad_spike" and "40×" in x.summary for x in R.evaluate(_facts(hbs)))
    hbs = _hbs(40, loss=lambda i: 0.500 + 0.0001 * (i % 2), gpu_util=90, cpu_pct=50)          # 40 × 5 min: the 2 h window is full
    assert any(x.rule == "plateau" for x in R.evaluate(_facts(hbs)))
    hbs = _hbs(vram_used_mb=lambda i: 8000 + 100 * i, vram_total_mb=16000, gpu_util=90, cpu_pct=50, loss=lambda i: 1 / (i + 1))
    assert any(x.rule == "vram_creep" for x in R.evaluate(_facts(hbs)))


def test_disk_trend_acts_before_disk_low():
    hbs = _hbs(disk_avail_gb=lambda i: 20 - 0.4 * i, gpu_util=90, cpu_pct=50, loss=lambda i: 1 / (i + 1))    # 8 GB left, falling ~4.8 GB/h → < 5 GB in < 1 h
    f = [x for x in R.evaluate(_facts(hbs)) if x.rule == "disk_trend"]
    assert f and f[0].suggested_action == "clean_disk" and f[0].action_params["keep"] == 2 and 0 < f[0].evidence["hours_left"] < 3
    assert not any(x.rule == "disk_low" for x in R.evaluate(_facts(hbs)))


def test_promotion_after_streak_and_demotion_on_failed_verification():
    db.jobs.put(Job(job_id="j1", status=JobStatus.RUNNING))
    for i in range(5):
        db.decisions.put(Decision(job_id="j1", action=Action.ROLLBACK_LAST_GOOD, verdict=Verdict.NEED_APPROVAL, status=DecisionStatus.DONE, approved_by="khaf",
                                  created_at=now() - timedelta(hours=5 - i)))
    out = ledger.apply_promotions(notify=None)
    assert out["promoted"] and db.jobs.get("j1").autonomy_overrides["rollback_last_good"] == "L2"
    assert any(a.to_dict()["action"] == "promote" for a in db.client().collection("audit").stream())
    # an L2 action whose verification failed → back to L1
    d = Decision(job_id="j1", action=Action.ROLLBACK_LAST_GOOD, verdict=Verdict.AUTO, status=DecisionStatus.DONE)
    db.decisions.put(d)
    inc = Incident(job_id="j1", rule="nan_loss", state=S.ESCALATED, decision_ids=[d.decision_id], verify={"kind": "rollback_last_good", "result": "fail"})
    db.incidents.put(inc)
    out = ledger.apply_promotions(notify=None)
    assert out["demoted"] and db.jobs.get("j1").autonomy_overrides["rollback_last_good"] == "L1"


def test_per_job_policy_overrides_global():
    fake = registry.compute(); inst = fake.add("vm1")
    db.jobs.put(Job(job_id="j1", instance_ref=inst.ref, status=JobStatus.RUNNING, run_id="r1", command="x"))
    db.client().collection("policies").document("j1").set({"autonomy": {"start_instance": "L1"}})
    pol = T._policy_for(db.jobs.get("j1"))
    assert pol["autonomy"]["start_instance"] == "L1" and pol["autonomy"]["stop_instance"] == "L2"


def test_hold_makes_actions_wait():
    fake = registry.compute(); inst = fake.add("stray")
    db.jobs.put(Job(job_id="jh", instance_ref=inst.ref, status=JobStatus.COMPLETE)); inst.job_id = "jh"
    assert ledger.hold("jh", 60, "khaf")["ok"]
    s = T.run_tick(); assert s["held"] == 1 and fake.describe(inst.ref).status == "RUNNING"


def test_false_positive_memory_withholds_the_action():
    from warden.executor import approvals
    fake = registry.compute(); inst = fake.add("vm2")
    job = Job(job_id="j2", instance_ref=inst.ref, status=JobStatus.RUNNING, run_id="r1", command="x"); db.jobs.put(job)
    for k in range(2):   # two dismissed idle alarms in the past week
        inc = Incident(job_id="j2", rule="idle", state=S.AWAITING_APPROVAL, summary="idle"); db.incidents.put(inc)
        assert approvals.false_positive(inc.incident_id, "khaf", "it was a long eval")["ok"]
    db.put_heartbeat(Heartbeat(job_id="j2", run_id="r1", ts=now() - timedelta(minutes=20), boot_id=inst.boot_id, phase="eval", step=100, gpu_util=0, cpu_pct=2))
    T._prev_status[inst.ref] = inst.status
    s = T.run_tick()
    idle = [i for i in db.incidents.list(job_id="j2", rule="idle") if i.state not in (S.FALSE_POSITIVE,)]
    assert idle and idle[0].severity == "info" and "withheld" in idle[0].summary and fake.describe(inst.ref).status == "RUNNING"


def test_learn_baselines_from_verified_data():
    from warden.core.models import Marker
    db.jobs.put(Job(job_id="j3", status=JobStatus.RUNNING, run_id="r1"))
    for i in range(12):
        db.put_heartbeat(Heartbeat(job_id="j3", run_id="r1", ts=now() - timedelta(minutes=12 - i), boot_id="b", phase="train", step=i * 100, step_per_s=2.0 + (i % 3) * 0.1))
    db.put_marker(Marker(job_id="j3", run_id="r1", kind="VERIFIED", valid=True, artifacts=[{"name": "ckpt_001000.npz", "bytes": 4096, "sha256": "x"}, {"name": "pred.csv", "bytes": 10, "sha256": "y"}]))
    out = ledger.learn_baselines()
    assert out["j3"]["ckpt_size_bytes"] == 4096 and out["j3"]["step_per_s_median"] == 2.1
    j = db.jobs.get("j3"); assert j.expect["ckpt_size_bytes"] == 4096 and j.expect["baseline_step_per_s"] == 2.1
