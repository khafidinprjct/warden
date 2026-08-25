"""Checklist A with the fake provider: spec → ledger first → zone choice on stock-out → VM → heartbeat RUNNING → VERIFIED → report → stop."""
import os, time
import pytest
from datetime import timedelta

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")

from warden import lifecycle
from warden.core.models import Heartbeat, IncidentState as S, JobStatus, now
from warden.executor import recovery
from warden.providers import registry
from warden.signals import ingest
from warden.store import firestore as db
from warden.watcher import tick as T


@pytest.fixture(autouse=True)
def fresh():
    registry._fake = None; T._prev_status.clear()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "cmd", "cmd_results", "stockouts", "reports", "postmortems", "costs"):
        for d in db.client().collection(coll).limit(300).stream():
            if coll == "runs":
                for h in d.reference.collection("heartbeats").limit(500).stream():
                    h.reference.delete()
            d.reference.delete()
    yield


SPEC = {"job_id": "toy-1", "command": "bash /opt/warden-src/toy_bootstrap.sh", "machine_type": "e2-medium", "zones": ["us-central1-a", "us-central1-b"],
        "expect": {"pred.csv": {"rows": 2000}}, "budget_cap_usd": 1.0}


def test_launch_picks_second_zone_on_stockout_and_ledger_is_first():
    fake = registry.compute(); fake.stock["us-central1-a"] = False
    r = lifecycle.launch(SPEC, actor="test")
    assert r["ok"] and r["instance_ref"] == "us-central1-b/warden-toy-1", r
    job = db.jobs.get("toy-1"); assert job.status == JobStatus.PENDING and job.instance_ref == r["instance_ref"] and job.zone_candidates == SPEC["zones"]
    assert db.stockout_recent("us-central1-a", "e2-medium")
    auds = [a.to_dict() for a in db.client().collection("audit").stream()]
    assert [a["phase"] for a in sorted(auds, key=lambda a: a["ts"])] == ["intent", "result", "intent", "result"]
    assert any(a["target"] == "us-central1-a/warden-toy-1" and a["ok"] is False for a in auds)
    inst = fake.describe(r["instance_ref"]); assert inst.managed and inst.job_id == "toy-1" and inst.labels["warden-role"] == "job"
    # first heartbeat → RUNNING (proof the machine and harness are alive)
    ingest.ingest_heartbeat({"job_id": "toy-1", "run_id": "r1", "boot_id": inst.boot_id, "phase": "train", "step": 50, "loss": 0.7, "cpu_pct": 80})
    assert db.jobs.get("toy-1").status == JobStatus.RUNNING


def test_launch_no_zone_left_fails_loudly():
    fake = registry.compute(); fake.stock["us-central1-a"] = False; fake.stock["us-central1-b"] = False
    r = lifecycle.launch(SPEC, actor="test")
    assert not r["ok"] and len(r["attempts"]) == 2 and db.jobs.get("toy-1").status == JobStatus.FAILED


def test_duplicate_job_id_rejected():
    fake = registry.compute(); lifecycle.launch(SPEC, actor="test")
    with pytest.raises(ValueError):
        lifecycle.launch(SPEC, actor="test")


def test_complete_job_is_stopped_and_reported(tmp_path, monkeypatch):
    fake = registry.compute(); r = lifecycle.launch(SPEC, actor="test"); ref = r["instance_ref"]; inst = fake.describe(ref)
    for i in range(12):
        db.put_heartbeat(Heartbeat(job_id="toy-1", run_id="r1", ts=now() - timedelta(minutes=12 - i), boot_id=inst.boot_id, phase="train", step=i * 100, loss=0.5, cpu_pct=80, gpu_util=0, disk_avail_gb=15))
    job = db.jobs.get("toy-1"); job.status = JobStatus.COMPLETE; job.run_id = "r1"; job.spent_usd = 0.42; db.jobs.put(job)
    rep = lifecycle.report(job)
    assert rep["spent_usd"] == 0.42 and rep["ettr"] is not None and db.client().collection("reports").document("toy-1").get().exists
    s = T.run_tick(); assert s["auto"] == 1
    inc = [i for i in db.incidents.list(job_id="toy-1") if i.rule == "complete_running"][0]
    assert fake.describe(ref).status == "STOPPED" and inc.state == S.VERIFYING
    assert recovery.process_verifying()["resolved"] == 1


def test_budget_exhausted_stops_machine():
    fake = registry.compute(); r = lifecycle.launch(SPEC, actor="test"); ref = r["instance_ref"]; inst = fake.describe(ref)
    job = db.jobs.get("toy-1"); job.status = JobStatus.RUNNING; job.spent_usd = 1.05; db.jobs.put(job)
    db.put_heartbeat(Heartbeat(job_id="toy-1", run_id="r1", ts=now(), boot_id=inst.boot_id, phase="train", step=100, loss=0.5, cpu_pct=80, gpu_util=90, disk_avail_gb=15))
    s = T.run_tick()
    rules = {i.rule for i in db.incidents.list(job_id="toy-1")}
    assert "budget_exhausted" in rules and fake.describe(ref).status == "STOPPED"


def test_preflight_fail_stops_machine():
    fake = registry.compute(); r = lifecycle.launch(SPEC, actor="test"); ref = r["instance_ref"]; inst = fake.describe(ref)
    ingest.ingest_marker({"job_id": "toy-1", "run_id": "", "kind": "PREFLIGHT_FAIL", "boot_id": inst.boot_id, "evidence": {"reason": "torch/cuda not ready"}})
    T._prev_status[ref] = inst.status
    s = T.run_tick()
    inc = [i for i in db.incidents.list(job_id="toy-1") if i.rule == "preflight_fail"]
    assert inc and "torch/cuda" in inc[0].summary and fake.describe(ref).status == "STOPPED"
