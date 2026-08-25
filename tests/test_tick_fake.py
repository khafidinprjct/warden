"""Tick end-to-end dengan fake GCE + Firestore emulator (tanpa LLM). Butuh emulator hidup."""
import os
import pytest
from datetime import timedelta

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")

from warden.core.models import Heartbeat, IncidentState, Job, JobStatus, Marker, now
from warden.providers import registry
from warden.store import firestore as db
from warden.watcher import tick as T


@pytest.fixture(autouse=True)
def fresh():
    registry._fake = None
    T._prev_status.clear()
    # bersihkan koleksi uji
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "notifications"):
        for d in db.client().collection(coll).limit(200).stream():
            d.reference.delete()
    yield


def _job(fake, name="vm1"):
    inst = fake.add(name)
    job = Job(job_id="j1", instance_ref=inst.ref, status=JobStatus.RUNNING, run_id="r1", phase="F3", command="/venv/bin/python x.py")
    db.jobs.put(job)
    for i in range(10):
        db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=now() - timedelta(minutes=10 - i), boot_id=inst.boot_id, phase="F3",
                                   step=i * 50, loss=0.4, gpu_util=90, cpu_pct=80, disk_avail_gb=40))
    return inst, job


def test_preempt_two_ticks_then_auto_start():
    fake = registry.compute(); inst, job = _job(fake)
    assert T.run_tick()["findings"] == 0
    fake.preempt(inst.ref)
    assert T.run_tick()["findings"] == 0
    s = T.run_tick(); assert s["auto"] == 1
    assert fake.describe(inst.ref).status == "RUNNING"
    inc = db.incidents.list(rule="preempted")[0]
    assert inc.state == IncidentState.RESOLVED
    auds = db.client().collection("audit").limit(10).stream()
    phases = sorted(a.to_dict()["phase"] for a in auds)
    assert phases == ["intent", "result"]


def test_unsafe_disk_config_only_notifies():
    fake = registry.compute(); inst, job = _job(fake)
    inst.boot_disk_auto_delete = True
    s = T.run_tick(); assert s["findings"] >= 1
    assert any(i.rule == "unsafe_config" for i in db.incidents.list(job_id="j1"))
    assert ("stop", inst.ref, False) not in fake.calls


def test_orphan_stopped_after_grace():
    fake = registry.compute(); inst = fake.add("stray")
    inst.labels = {"warden-managed": "true"}; inst.managed = True
    # tanpa job, tanpa denyut → boot_age dianggap lama (999) → yatim → stop otomatis (L2)
    s = T.run_tick(); assert s["auto"] == 1
    assert fake.describe(inst.ref).status == "STOPPED"


def test_dedupe_prevents_repeat():
    fake = registry.compute(); inst, job = _job(fake)
    fake.preempt(inst.ref); T.run_tick(); T.run_tick()
    n1 = len(db.incidents.list(rule="preempted"))
    fake.preempt(inst.ref)          # preempt lagi dengan boot_id BARU → insiden baru sah; kunci dedupe memakai boot_id
    T.run_tick(); T.run_tick()
    assert len(db.incidents.list(rule="preempted")) == n1 + 1
