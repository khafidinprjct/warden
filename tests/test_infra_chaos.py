"""Phase 12 — infrastructure chaos: Warden degrades in a controlled way when Gemini, Discord or Firestore misbehave."""
import os, time
os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081"); os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test"); os.environ.setdefault("WARDEN_PROVIDER", "fake")
from warden.core.models import IncidentState, Job, JobStatus
from warden.providers import registry
from warden.store import firestore as db
from warden.watcher import tick as T


def _reset():
    registry._fake = None; T._prev_status.clear()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "notifications", "policies", "health"):
        for d in db.client().collection(coll).limit(300).stream():
            d.reference.delete()


def test_discord_down_does_not_block_actions():
    _reset()
    calls = {"n": 0}

    def broken_notify(inc, dec, text):
        calls["n"] += 1; raise ConnectionError("discord unreachable")
    fake = registry.compute(); inst = fake.add("vm-2"); db.jobs.put(Job(job_id="j2", instance_ref=inst.ref, status=JobStatus.RUNNING)); inst.job_id = "j2"
    T.run_tick(notify=broken_notify); fake.preempt(inst.ref)
    T.run_tick(notify=broken_notify); s = T.run_tick(notify=broken_notify)          # stopped_external needs two ticks TERMINATED
    assert s["auto"] == 1 and fake.describe(inst.ref).status == "RUNNING"          # start executed despite notify failing
    assert calls["n"] >= 1 and not s["errors"]
    h = db.client().collection("health").document("notify").get().to_dict()
    assert h and h.get("ok") is False                                              # failure is visible, not silent


def test_gemini_failures_open_circuit_and_fall_back_to_deterministic(monkeypatch):
    _reset()
    from warden.agents import pipeline as P
    from warden.core.models import Incident, Evidence
    def boom(*a, **k): raise RuntimeError("500 from Vertex")
    monkeypatch.setattr(P, "diagnose", boom)
    monkeypatch.setattr(P, "_log_lines", lambda *a, **k: ["Traceback", "RuntimeError: x"], raising=False)
    for i in range(6):
        inc = Incident(job_id="j1", rule="stuck", severity="critical", summary=f"stuck {i}", state=IncidentState.DIAGNOSING); db.incidents.put(inc)
        P.process_diagnosing()
    h = db.client().collection("health").document("llm_circuit").get().to_dict() or {}
    escalated = [i for i in db.incidents.list(limit=50) if i.state == IncidentState.ESCALATED]
    assert escalated, "incidents must escalate to a human when the LLM is unavailable"
    assert h.get("ok") is False and "OPEN" in str(h.get("last_error", "")), h   # breaker opened after 5 consecutive failures


def test_slow_firestore_tick_still_completes_and_heartbeats(monkeypatch):
    _reset()
    fake = registry.compute(); inst = fake.add("vm-3"); db.jobs.put(Job(job_id="j3", instance_ref=inst.ref, status=JobStatus.RUNNING)); inst.job_id = "j3"
    orig = db.jobs.list
    def slow_list(*a, **k):
        time.sleep(0.4); return orig(*a, **k)
    monkeypatch.setattr(db.jobs, "list", slow_list)
    t0 = time.time(); s = T.run_tick(); dt = time.time() - t0
    assert not s["errors"] and dt < 10
    hb = db.client().collection("health").document("watcher").get().to_dict()
    assert hb and hb.get("ok") is True and hb.get("last_ok_at")
