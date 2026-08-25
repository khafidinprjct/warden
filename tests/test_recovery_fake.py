"""Closing the loop (checklist D/E/F/H3): ladders, world-verification, next hypothesis, memory, idempotency — fake GCE + emulator."""
import os
import pytest
from datetime import timedelta

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")

from warden.core.models import Heartbeat, IncidentState as S, Job, JobStatus, now
from warden.executor import approvals, recovery
from warden.providers import registry
from warden.store import firestore as db
from warden.watcher import tick as T


@pytest.fixture(autouse=True)
def fresh():
    registry._fake = None; T._prev_status.clear()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "notifications", "cmd", "cmd_results", "stockouts", "postmortems", "costs"):
        for d in db.client().collection(coll).limit(300).stream():
            if coll == "runs":
                for h in d.reference.collection("heartbeats").limit(500).stream():   # subcollections do not die with the parent
                    h.reference.delete()
            d.reference.delete()
    yield


def _job(fake, name="vm1", **kw):
    inst = fake.add(name)
    job = Job(job_id="j1", instance_ref=inst.ref, status=JobStatus.RUNNING, run_id="r1", phase="train", command="/venv/bin/python x.py", **kw)
    db.jobs.put(job)
    for i in range(10):
        db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=now() - timedelta(minutes=10 - i), boot_id=inst.boot_id, phase="train",
                                   step=i * 50, loss=0.4, gpu_util=90, cpu_pct=80, disk_avail_gb=40, procs=[{"pid": 7, "ppid": 1, "cmd": "/venv/bin/python x.py"}]))
    return inst, job


def _hb(job_id, boot_id, run_id, step, **kw):
    db.put_heartbeat(Heartbeat(job_id=job_id, run_id=run_id, ts=now() + timedelta(seconds=step / 100.0), boot_id=boot_id, phase="train", step=step, loss=0.3,
                               procs=[{"pid": 9, "ppid": 1, "cmd": "x"}], **kw))


def _expire(inc):
    inc.verify["deadline"] = (now() - timedelta(seconds=1)).isoformat(); db.incidents.put(inc)


def test_start_stockout_then_relocate_needs_approval_then_verified():
    fake = registry.compute(); inst, job = _job(fake)
    fake.preempt(inst.ref); T.run_tick()
    fake.fail_next[inst.ref] = "ZONE_RESOURCE_POOL_EXHAUSTED: The zone 'us-central1-a' does not have enough resources"
    s = T.run_tick(); assert s["auto"] == 1
    inc = db.incidents.list(rule="preempted")[0]
    # start failed → next hypothesis relocate_zone is L1 → awaiting approval, with the reason on the decision
    assert inc.state == S.AWAITING_APPROVAL and inc.attempt == 1
    dec = [d for d in db.decisions.list(incident_id=inc.incident_id) if d.action == "relocate_zone"][0]
    assert dec.verdict == "NEED_APPROVAL" and "stock-out" in dec.explain[0]
    assert db.stockout_recent("us-central1-a", "e2-medium")
    fake.instances[inst.ref].status = "STOPPED"          # relocation requires a stopped source
    r = approvals.approve(dec.decision_id, "khaf"); assert r["ok"], r
    inc = db.incidents.get(inc.incident_id); assert inc.state == S.VERIFYING and inc.verify["kind"] == "relocate_zone"
    new_ref = db.jobs.get("j1").instance_ref
    assert new_ref != inst.ref and new_ref.startswith("us-central1-b/")
    for i in range(3):
        _hb("j1", fake.describe(new_ref).boot_id, "r2", 100 + i * 50)
    assert recovery.process_verifying()["resolved"] == 1
    assert db.incidents.get(inc.incident_id).state == S.RESOLVED


def test_disk_low_clean_then_verified_by_heartbeat():
    fake = registry.compute(); inst, job = _job(fake)
    db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=now(), boot_id=inst.boot_id, phase="train", step=600, loss=0.4, gpu_util=90, cpu_pct=80, disk_avail_gb=3.0,
                               procs=[{"pid": 7, "ppid": 1, "cmd": "x"}]))
    s = T.run_tick(); assert s["auto"] == 1
    inc = [i for i in db.incidents.list(job_id="j1") if i.rule == "disk_low"][0]
    cmd = db.client().collection("cmd").document("j1").get().to_dict()
    assert cmd["cmd"] == "clean_disk" and cmd["args"]["keep"] == 2 and cmd["sig"]
    assert inc.state == S.VERIFYING
    db.cmd_result_put("j1", {"nonce": cmd["nonce"], "ok": True, "freed_bytes": 5_000_000_000, "cmd": "clean_disk"})
    _hb("j1", inst.boot_id, "r1", 650, disk_avail_gb=8.0)
    assert recovery.process_verifying()["resolved"] == 1
    assert db.incidents.get(inc.incident_id).state == S.RESOLVED


def test_clean_disk_nothing_to_free_then_resize_needs_approval():
    fake = registry.compute(); inst, job = _job(fake)
    db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=now(), boot_id=inst.boot_id, phase="train", step=600, loss=0.4, gpu_util=90, cpu_pct=80, disk_avail_gb=3.0, procs=[{"pid": 7, "ppid": 1, "cmd": "x"}]))
    T.run_tick()
    inc = [i for i in db.incidents.list(job_id="j1") if i.rule == "disk_low"][0]
    cmd = db.client().collection("cmd").document("j1").get().to_dict()
    db.cmd_result_put("j1", {"nonce": cmd["nonce"], "ok": True, "freed_bytes": 0, "cmd": "clean_disk"})
    r = recovery.process_verifying(); assert r["advanced"] == 1
    inc = db.incidents.get(inc.incident_id)
    assert inc.state == S.AWAITING_APPROVAL and inc.attempt == 1
    dec = [d for d in db.decisions.list(incident_id=inc.incident_id) if d.action == "resize_disk"][0]
    assert dec.dry_run_plan["plan"]["to_gb"] == 30 and "nothing left to clean" in dec.explain[0]


def test_deadline_fail_then_ladder_exhausted_escalates():
    fake = registry.compute(); inst, job = _job(fake)
    fake.preempt(inst.ref); T.run_tick(); T.run_tick(); T.run_tick()
    inc = db.incidents.list(rule="preempted")[0]; assert inc.state == S.VERIFYING
    _expire(inc)
    recovery.process_verifying()                     # no fresh heartbeat → fail → relocate (L1) awaits approval
    inc = db.incidents.get(inc.incident_id); assert inc.state == S.AWAITING_APPROVAL
    dec = [d for d in db.decisions.list(incident_id=inc.incident_id) if d.action == "relocate_zone"][0]
    approvals.deny(dec.decision_id, "khaf")
    inc = db.incidents.get(inc.incident_id); assert inc.state == S.CLOSED
    # a fresh incident whose ladder runs dry escalates with a human-readable reason
    inc2 = db.incidents.list(rule="preempted")[0]
    inc2.ladder = []; inc2.state = S.VERIFYING; inc2.verify = {"kind": "start_instance", "since": now().isoformat(), "deadline": (now() - timedelta(seconds=1)).isoformat(), "baseline": {"boot_id": "zzz"}, "params": {}}
    db.incidents.put(inc2); recovery.process_verifying()
    inc2 = db.incidents.get(inc2.incident_id); assert inc2.state == S.ESCALATED and "exhausted" in inc2.timeline[-1]["note"]


def test_memory_puts_the_proven_action_first():
    fake = registry.compute(); inst, job = _job(fake)
    db.client().collection("postmortems").document("inc_old").set({"incident_id": "inc_old", "job_id": "j1", "rule": "preempted", "category": None, "ok": True,
                                                                     "outcome": "RESOLVED", "closed_at": now().isoformat(),
                                                                     "actions": [{"action": "start_instance", "status": "DONE"}, {"action": "relocate_zone", "status": "DONE", "params": {"target_zone": "us-central1-c"}}]})
    fake.preempt(inst.ref); T.run_tick(); T.run_tick()
    fake.instances[inst.ref].status = "STOPPED"
    s = T.run_tick()
    inc = db.incidents.list(rule="preempted")[0]
    assert inc.memory_ref == "inc_old"
    dec = db.decisions.get(inc.decision_ids[0])
    assert dec.action == "relocate_zone" and dec.explain[0].startswith("memory: same pattern as inc_old")


def test_idempotent_ticks_do_not_double_act():
    fake = registry.compute(); inst, job = _job(fake)
    fake.preempt(inst.ref); T.run_tick(); T.run_tick(); T.run_tick()
    n_start = sum(1 for c in fake.calls if c[0] == "start" and c[2] is False)
    T.run_tick(); T.run_tick(); recovery.process_verifying()
    assert sum(1 for c in fake.calls if c[0] == "start" and c[2] is False) == n_start == 1
    assert len(db.incidents.list(rule="preempted")) == 1


def test_every_action_has_a_dry_run_plan():
    from warden.core.models import Action, Decision
    fake = registry.compute(); inst, job = _job(fake)
    from warden.executor import registry as ex
    for a in Action:
        dec = Decision(job_id="j1", action=a, params={"instance_ref": inst.ref, "path": "x", "ckpt": "ckpt_1.npz"})
        plan = ex.dry_run(dec, fake)
        assert plan["ok"], (a, plan)


def test_notify_on_critical_or_unknown_escalates_not_resolves():
    from warden.core.models import Action, Decision, Incident, IncidentState as S
    from warden.core.state_machine import transition
    from warden.providers.base import OpResult
    inc = Incident(job_id="j1", rule="run_fin_nonzero", severity="critical", summary="exit 1", diagnosis={"category": "unknown", "needs_human": True})
    transition(inc, S.TRIAGED); transition(inc, S.DECIDED); transition(inc, S.EXECUTING); db.incidents.put(inc)
    dec = Decision(job_id="j1", incident_id=inc.incident_id, action=Action.NOTIFY); db.decisions.put(dec)
    recovery.after_execute(inc, dec, OpResult(True, "notify", observed="sent"))
    assert inc.state == S.ESCALATED and "human" in inc.timeline[-1]["note"]
    info = Incident(job_id="j1", rule="budget_80", severity="warning", summary="80%")
    transition(info, S.TRIAGED); transition(info, S.DECIDED); transition(info, S.EXECUTING); db.incidents.put(info)
    dec2 = Decision(job_id="j1", incident_id=info.incident_id, action=Action.NOTIFY); db.decisions.put(dec2)
    recovery.after_execute(info, dec2, OpResult(True, "notify", observed="sent"))
    assert info.state == S.RESOLVED


def test_preempt_storm_skips_start_and_relocates():
    """B4: three preemptions within an hour → no fourth start; relocate (L1) is proposed with the storm as the reason."""
    fake = registry.compute(); inst, job = _job(fake)
    for _ in range(3):
        fake.preempt(inst.ref)
    T.run_tick(); s = T.run_tick()
    inc = [i for i in db.incidents.list(job_id="j1") if i.rule == "preempt_storm"][0]
    assert "3× in 60 min" in inc.summary and inc.state == S.AWAITING_APPROVAL
    dec = db.decisions.get(inc.decision_ids[0]); assert dec.action == "relocate_zone"
    assert not any(c[0] == "start" and c[2] is False for c in fake.calls)
    # the on-demand exit rung exists but is priced: +233 % > 50 % → denied by policy when reached
    assert inc.ladder and inc.ladder[0]["params"].get("spot") is False
