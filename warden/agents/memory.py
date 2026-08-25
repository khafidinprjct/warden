"""Incident memory (Phase A-2): one postmortem per finished incident, embedded and searchable so later investigations
retrieve what happened before ("context retrieval based on past interactions"). Deterministic text, no LLM cost to write;
embeddings via Vertex AI text embeddings; nearest-neighbour recall via Firestore vector search when an index exists,
otherwise a same-job / same-rule fallback."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from warden.config import settings
from warden.core.models import now
from warden.store import firestore as db

TERMINAL = ("RESOLVED", "ESCALATED", "CLOSED", "FALSE_POSITIVE")
COLL = "postmortems"
EMBED_MODEL = "gemini-embedding-001"
DIM = 768


def _s(x: Any) -> str:
    return str(x).split(".")[-1]


def compose(inc, decs: list) -> dict:
    """Ten-line postmortem, deterministic: symptom, evidence, diagnosis, action, outcome, cost, lesson."""
    d = inc.diagnosis or {}; cc = inc.crosscheck or {}
    actions = [{"action": _s(x.action), "verdict": _s(x.verdict), "autonomy": _s(x.autonomy), "status": _s(x.status),
                "params": {k: v for k, v in (x.params or {}).items() if k not in ("instance_ref", "run_id", "reason", "new_instance_ref")},
                "observed": (x.result or {}).get("observed", ""), "error": (x.result or {}).get("error", ""), "by": x.approved_by} for x in decs]
    ok = _s(inc.state) in ("RESOLVED", "CLOSED")
    lesson = d.get("falsifiable_check") or (f"{inc.rule}: handled by rule without LLM" if not d else "")
    evidence = (d.get("evidence_quotes") or [])[:3]
    text = (f"Incident {inc.rule} on job {inc.job_id or '-'} ({inc.instance_ref or '-'}). Symptom: {inc.summary}. "
            f"Diagnosis: {d.get('category', 'rule-based')} confidence {cc.get('adjusted_confidence', d.get('confidence', '-'))}. "
            f"Actions: {', '.join(f'{a['action']}={a['status']}' for a in actions) or 'none'}. Outcome: {_s(inc.state)}. "
            f"LLM cost ${inc.llm_cost_usd:.3f}. Lesson: {lesson}")
    return {"incident_id": inc.incident_id, "job_id": inc.job_id, "instance_ref": inc.instance_ref, "rule": inc.rule, "severity": inc.severity,
            "attempts": inc.attempt, "verified": (inc.verify or {}).get("result", ""), "memory_ref": inc.memory_ref,
            "category": d.get("category"), "outcome": _s(inc.state), "ok": ok, "actions": actions, "evidence": evidence, "lesson": lesson,
            "llm_cost_usd": inc.llm_cost_usd, "opened_at": inc.created_at.isoformat(), "closed_at": inc.updated_at.isoformat(),
            "duration_s": int((inc.updated_at - inc.created_at).total_seconds()), "text": text, "created_at": now().isoformat()}


def embed(text: str) -> list[float] | None:
    """Vertex AI embedding; None when unavailable (memory still works by job/rule)."""
    try:
        from google import genai
        client = genai.Client(vertexai=True, project=settings.project, location="global")
        r = client.models.embed_content(model=EMBED_MODEL, contents=text, config={"output_dimensionality": DIM, "task_type": "RETRIEVAL_DOCUMENT"})
        return list(r.embeddings[0].values)
    except Exception as e:  # noqa: BLE001
        db.health("embeddings", False, str(e)[:200])
        return None


def write_postmortems(max_n: int = 20) -> int:
    """Called by the steward sweep: every incident in a terminal state without a postmortem gets one."""
    n = 0
    coll = db.client().collection(COLL)
    for inc in sorted(db.incidents.list(limit=300), key=lambda i: i.updated_at, reverse=True):
        if _s(inc.state) not in TERMINAL or coll.document(inc.incident_id).get().exists:
            continue
        decs = [db.decisions.get(x) for x in inc.decision_ids]; decs = [x for x in decs if x]
        pm = compose(inc, decs)
        vec = embed(pm["text"])
        if vec is not None:
            from google.cloud.firestore_v1.vector import Vector
            pm["embedding"] = Vector(vec)
        coll.document(inc.incident_id).set(pm); n += 1
        if n >= max_n:
            break
    if n:
        db.health("memory", True)
    return n


def recall(job_id: str = "", rule: str = "", query: str = "", n: int = 5) -> list[dict]:
    """Similar past incidents. Vector search when a query is given and the index exists; else newest postmortems of the job/rule."""
    coll = db.client().collection(COLL)
    out: list[dict] = []
    if query:
        vec = embed(query)
        if vec is not None:
            try:
                from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
                from google.cloud.firestore_v1.vector import Vector
                q = coll.find_nearest(vector_field="embedding", query_vector=Vector(vec), distance_measure=DistanceMeasure.COSINE, limit=n)
                out = [_strip(d.to_dict()) for d in q.get()]
            except Exception as e:  # noqa: BLE001 — no vector index yet or API error → fallback below
                db.health("memory", False, str(e)[:200])
    if not out:
        docs = [d.to_dict() for d in coll.limit(200).stream()]
        docs = [x for x in docs if (not job_id or x.get("job_id") == job_id) and (not rule or x.get("rule") == rule)]
        out = [_strip(x) for x in sorted(docs, key=lambda x: x.get("closed_at", ""), reverse=True)[:n]]
    return out


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "embedding"}
