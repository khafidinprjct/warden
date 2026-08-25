"""Phase A: Investigator tools (read-only, emulator), incident memory (postmortems + recall fallback), pipeline integration with a stubbed LLM."""
import os
os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081"); os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test"); os.environ.setdefault("WARDEN_PROVIDER", "fake")
from datetime import timedelta
from pathlib import Path
from warden.agents import investigator as I, memory as M, pipeline as P
from warden.agents.schemas import Diagnosis
from warden.core.models import Decision, DecisionStatus, Heartbeat, Incident, IncidentState, Job, JobStatus, Marker, now
from warden.providers import registry
from warden.store import firestore as db


def _reset():
    registry._fake = None
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "runs", "health", "costs", "postmortems"):
        for d in db.client().collection(coll).limit(300).stream():
            d.reference.delete()
    for jid in ("j1",):
        for d in db.client().collection("runs").document(jid).collection("heartbeats").limit(300).stream():
            d.reference.delete()


def _seed_job():
    Path("data/gcs/j1").mkdir(parents=True, exist_ok=True)
    Path("data/gcs/j1/tail.log").write_text("\n".join([f"step {i} loss {1/(i+1):.3f}" for i in range(1, 300)] + ["Traceback (most recent call last):", "  File x.py line 26", "EOFError: No data left in file", "EXIT=1"]))
    db.jobs.put(Job(job_id="j1", status=JobStatus.RUNNING, run_id="r1", phase="train", last_step=1700))
    t0 = now() - timedelta(minutes=10)
    for i in range(10):
        db.put_heartbeat(Heartbeat(job_id="j1", run_id="r1", ts=t0 + timedelta(minutes=i), phase="train", step=100 * i, loss=1.0 / (i + 1), cpu_pct=30))
    db.put_marker(Marker(job_id="j1", run_id="r1", kind="RUN_FIN", exit_code=1, valid=True, artifacts=[{"path": "/a/ckpt_001742.npz", "bytes": 0, "sha256": "0" * 64}]))


def test_tools_read_only_and_cited():
    _reset(); _seed_job()
    w = I.get_log_window("j1", 298, 310); assert w["total_lines"] == 303 and any("EOFError" in l for l in w["lines"])
    s = I.search_log("j1", r"EOFError|Traceback"); assert s["n_hits"] == 2 and s["hits"][0]["line"] == 300
    h = I.get_heartbeats("j1", 5); assert len(h["heartbeats"]) == 5 and h["heartbeats"][-1]["step"] == 900
    a = I.get_artifacts("j1"); assert a["run_fin"]["exit_code"] == 1 and a["run_fin"]["artifacts"][0]["bytes"] == 0
    assert I.get_instance("us-central1-a/nope") == {"error": "instance not found"}
    assert I.search_log("j1", "(")["error"].startswith("bad pattern")


def test_postmortem_written_once_and_recalled_without_embeddings(monkeypatch):
    _reset(); _seed_job()
    monkeypatch.setattr(M, "embed", lambda text: None)                     # no Vertex in unit tests
    inc = Incident(job_id="j1", rule="run_fin_nonzero", severity="critical", summary="job j1 ended with exit=1", state=IncidentState.RESOLVED,
                   diagnosis={"category": "checkpoint_corrupt", "confidence": 0.9, "falsifiable_check": "np.load(ckpt) → EOFError", "evidence_quotes": ["EOFError: No data left in file"]},
                   crosscheck={"passed": True, "adjusted_confidence": 0.9}, llm_cost_usd=0.012)
    dec = Decision(incident_id=inc.incident_id, job_id="j1", action="rollback_last_good", status=DecisionStatus.DONE, result={"observed": "ok"}); db.decisions.put(dec)
    inc.decision_ids.append(dec.decision_id); db.incidents.put(inc)
    db.incidents.put(Incident(job_id="j1", rule="stuck", severity="warning", summary="still open", state=IncidentState.AWAITING_APPROVAL))
    assert M.write_postmortems() == 1 and M.write_postmortems() == 0               # idempotent, open incidents skipped
    pm = M.recall(job_id="j1"); assert len(pm) == 1 and pm[0]["category"] == "checkpoint_corrupt" and "rollback_last_good=DONE" in pm[0]["text"]
    assert "embedding" not in pm[0]
    hist = I.get_incident_history("j1"); assert hist["postmortems"][0]["lesson"].startswith("np.load")


def test_pipeline_uses_investigation_notes_and_records_evidence(monkeypatch):
    _reset(); _seed_job()
    seen = {}
    def fake_investigate(job_id, summary, instance_ref="", findings=None, model=None, **kw):
        return "Hypotheses: 1) truncated checkpoint. Evidence: line 300 EOFError.", [{"tool": "search_log", "args": {"pattern": "EOFError"}, "result_preview": "{}"}], {"cost_usd": 0.004, "model": "gemini-3.5-flash", "tool_calls": 1}
    def fake_diagnose(job_card, findings, hbsum, lines, model=None, investigation="", **kw):
        seen["investigation"] = investigation
        d = Diagnosis(category="data_error", confidence=0.9, evidence_lines=[300], evidence_quotes=["EOFError: No data left in file"], transient_or_permanent="permanent",
                      recommended_action="escalate", blast_radius="this_run", needs_human=True, human_summary="Checkpoint truncated at preemption.", falsifiable_check="np.load fails", root_cause="Emergency checkpoint truncated by the preemption")
        return d, {"cost_usd": 0.01, "model": model or "gemini-3.5-flash", "prompt_tokens": 1, "output_tokens": 1}
    monkeypatch.setattr(P, "investigate", fake_investigate); monkeypatch.setattr(P, "diagnose", fake_diagnose)
    inc = Incident(job_id="j1", rule="run_fin_nonzero", severity="critical", summary="job j1 ended with exit=1", state=IncidentState.DIAGNOSING); db.incidents.put(inc)
    P.process_diagnosing()
    inc = db.incidents.get(inc.incident_id)
    assert "truncated checkpoint" in seen["investigation"]
    kinds = [db.evidence.get(e).kind for e in inc.evidence_ids if db.evidence.get(e)]
    assert "investigation" in kinds and inc.llm_cost_usd >= 0.014
    assert any("investigated: 1 tool calls" in t.get("note", "") for t in inc.timeline)
