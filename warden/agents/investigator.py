"""Investigator (Phase A-1): an ADK agent that gathers its own evidence with read-only tools before the Diagnostician decides.
The tools read Firestore / Cloud Storage only; nothing here can act on infrastructure. Bounded: ≤ MAX_TOOL_CALLS tool calls per investigation."""
from __future__ import annotations

import asyncio
import contextvars
import json
import re
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from warden.config import settings
from warden.store import firestore as db

MAX_TOOL_CALLS = 4
_budget: contextvars.ContextVar[dict | None] = contextvars.ContextVar("investigator_budget", default=None)


def _spend() -> dict | None:
    """Hard tool budget: after MAX_TOOL_CALLS every tool returns an error instead of data (the prompt asks for ≤ 4; this enforces it)."""
    b = _budget.get()
    if b is None:            # called outside an investigation (tests, concierge): no budget
        return None
    if b["left"] <= 0:
        return {"error": f"tool budget exhausted ({MAX_TOOL_CALLS} calls) — write the investigation note now"}
    b["left"] -= 1
    return None
PRICE = {"gemini-3.5-flash": (1.5, 9.0), "gemini-3.5-flash-lite": (0.3, 2.5), "gemini-3.7-flash": (0.75, 3.75)}


# ---------- read-only tools (plain functions → ADK FunctionTools) ----------
def _log_lines(job_id: str, run_id: str = "") -> list[str]:
    from warden.agents.pipeline import read_log_tail
    return read_log_tail(job_id, n=100000, run_id=run_id)


def get_log_window(job_id: str, start_line: int, end_line: int, run_id: str = "") -> dict:
    """Return numbered log lines [start_line, end_line] (1-based, inclusive, at most 60 lines) of the job's log; pass run_id to read a specific run's log."""
    if (err := _spend()):
        return err
    lines = _log_lines(job_id, run_id)
    s = max(1, int(start_line)); e = min(len(lines), int(end_line), s + 59)
    return {"job_id": job_id, "total_lines": len(lines), "lines": [f"{i:5d}| {lines[i - 1][:300]}" for i in range(s, e + 1)]}


def search_log(job_id: str, pattern: str, max_hits: int = 12, run_id: str = "") -> dict:
    """Regex search over the job's log (case-insensitive); pass run_id to search a specific run's log. Returns line numbers and text."""
    if (err := _spend()):
        return err
    lines = _log_lines(job_id, run_id)
    try:
        rx = re.compile(pattern, re.I)
    except re.error as e:
        return {"error": f"bad pattern: {e}"}
    hits = [{"line": i + 1, "text": l[:300]} for i, l in enumerate(lines) if rx.search(l)]
    return {"job_id": job_id, "total_lines": len(lines), "hits": hits[: min(int(max_hits), 12)], "n_hits": len(hits)}


def get_heartbeats(job_id: str, n: int = 20) -> dict:
    """Recent heartbeats (oldest first): ts, run_id, phase, step, loss, grad_norm, cpu_pct, gpu_util, disk_avail_gb, synthetic."""
    if (err := _spend()):
        return err
    hbs = db.recent_heartbeats(job_id, min(int(n), 40))
    return {"job_id": job_id, "heartbeats": [{"ts": h.ts.isoformat(), "run_id": h.run_id, "phase": h.phase, "step": h.step, "loss": h.loss, "grad_norm": h.grad_norm,
                                              "cpu_pct": h.cpu_pct, "gpu_util": h.gpu_util, "disk_avail_gb": h.disk_avail_gb, "synthetic": h.synthetic} for h in hbs]}


def get_artifacts(job_id: str) -> dict:
    """Artifacts declared by the latest RUN_FIN (name, bytes, sha256), the last VERIFIED marker and the last known-good checkpoint."""
    if (err := _spend()):
        return err
    job = db.jobs.get(job_id)
    if not job:
        return {"error": "job not found"}
    fin = db.get_marker(job_id, job.run_id, "RUN_FIN"); ver = db.get_marker(job_id, job.run_id, "VERIFIED")
    return {"job_id": job_id, "run_id": job.run_id, "status": str(job.status), "phase": job.phase, "last_step": job.last_step,
            "run_fin": ({"exit_code": fin.exit_code, "ts": fin.ts.isoformat(), "artifacts": [{"name": a["path"].split("/")[-1], "bytes": a.get("bytes"), "sha256": str(a.get("sha256", ""))[:12]} for a in fin.artifacts][:20], "n_artifacts": len(fin.artifacts),
                         "evidence": fin.evidence} if fin else None),
            "verified": ({"ts": ver.ts.isoformat(), "n": len(ver.artifacts)} if ver else None), "last_good_ckpt": job.last_good_ckpt, "expect": job.expect}


def get_incident_history(job_id: str, n: int = 5) -> dict:
    """Past incidents of this job (rule, state, diagnosis category, action, outcome) and remembered postmortems of similar incidents."""
    if (err := _spend()):
        return err
    from warden.agents import memory
    incs = sorted(db.incidents.list(job_id=job_id, limit=50), key=lambda i: i.created_at)[-int(n):]
    out = []
    for i in incs:
        decs = [db.decisions.get(d) for d in i.decision_ids]
        out.append({"incident_id": i.incident_id, "rule": i.rule, "state": str(i.state), "opened": i.created_at.isoformat(),
                    "category": (i.diagnosis or {}).get("category"), "actions": [f"{d.action}:{d.status}" for d in decs if d]})
    return {"job_id": job_id, "incidents": out, "postmortems": [{k: v for k, v in p.items() if k in ("rule", "category", "outcome", "actions", "lesson", "duration_s", "closed_at")} for p in memory.recall(job_id=job_id, n=3)]}


def get_instance(instance_ref: str) -> dict:
    """Current provider view of an instance: status, machine type, spot, price, last stop time, boot id."""
    if (err := _spend()):
        return err
    from warden.providers.registry import compute
    i = compute().describe(instance_ref)
    if not i:
        return {"error": "instance not found"}
    return {"ref": i.ref, "status": str(i.status), "machine_type": i.machine_type, "spot": i.spot, "hourly_price_usd": i.hourly_price_usd,
            "last_stop_at": i.last_stop_at.isoformat() if i.last_stop_at else None, "boot_id": i.boot_id, "termination_action": i.termination_action}


TOOLS = [get_log_window, search_log, get_heartbeats, get_artifacts, get_incident_history, get_instance]

SYSTEM = (
    "You are the investigator of an SRE system for long-running compute jobs. You are given an incident summary and a job id. "
    "Gather the evidence a diagnostician needs, using the read-only tools: widen or search the log around the failure, check heartbeats "
    "(is the step still advancing? loss finite? disk?), check artifacts (did RUN_FIN declare them? sizes sane?), check the instance, and "
    "check past incidents/postmortems of this job for a repeat pattern. Use at most 4 tool calls; stop as soon as the evidence is sufficient. "
    "Never guess: every claim must cite a tool result (log line numbers, timestamps, byte counts). "
    "Finish with a compact investigation note in English with these headings: Hypotheses (ranked), Evidence (with citations), "
    "Ruled out, Recommended check for the diagnostician."
)


def build_agent(model: str | None = None) -> LlmAgent:
    return LlmAgent(name="investigator", model=model or settings.gemini_model, instruction=SYSTEM, tools=TOOLS, output_key="investigation",
                    generate_content_config=types.GenerateContentConfig(temperature=0.1))


async def investigate_async(job_id: str, incident_summary: str, instance_ref: str = "", findings: list[dict] | None = None,
                            model: str | None = None, run_id: str = "") -> tuple[str, list[dict], dict[str, Any]]:
    """Returns (investigation_note, tool_log, usage). tool_log = [{tool, args, result_preview}], bounded by MAX_TOOL_CALLS."""
    _budget.set({"left": MAX_TOOL_CALLS})
    agent = build_agent(model)
    runner = InMemoryRunner(agent=agent, app_name="warden")
    session = await runner.session_service.create_session(app_name="warden", user_id="warden")
    brief = {"job_id": job_id, "instance_ref": instance_ref, "incident": incident_summary, "deterministic_findings": (findings or [])[:6], "run_id": run_id}
    msg = types.Content(role="user", parts=[types.Part(text="## brief\n" + json.dumps(brief, ensure_ascii=False)[:3000])])
    usage: dict[str, Any] = {"prompt_tokens": 0, "output_tokens": 0}
    tool_log: list[dict] = []; final_text = ""; pending: dict[str, dict] = {}
    async for ev in runner.run_async(user_id="warden", session_id=session.id, new_message=msg):
        if getattr(ev, "usage_metadata", None):
            usage["prompt_tokens"] += getattr(ev.usage_metadata, "prompt_token_count", 0) or 0
            usage["output_tokens"] += getattr(ev.usage_metadata, "candidates_token_count", 0) or 0
        for fc in ev.get_function_calls() or []:
            pending[fc.id or fc.name] = {"tool": fc.name, "args": dict(fc.args or {})}
        for fr in ev.get_function_responses() or []:
            rec = pending.pop(fr.id or fr.name, {"tool": fr.name, "args": {}})
            rec["result_preview"] = json.dumps(fr.response, ensure_ascii=False, default=str)[:400]
            tool_log.append(rec)
        if ev.is_final_response() and ev.content and ev.content.parts:
            final_text = "".join(p.text or "" for p in ev.content.parts)
        if len(tool_log) >= MAX_TOOL_CALLS and not final_text:
            pass  # the model is told the budget; the runner finishes naturally after the final text
    used = model or settings.gemini_model
    pin, pout = PRICE.get(used, (1.5, 9.0))
    usage["cost_usd"] = round(usage["prompt_tokens"] / 1e6 * pin + usage["output_tokens"] / 1e6 * pout, 6)
    usage["model"] = used; usage["tool_calls"] = len(tool_log)
    return final_text.strip(), tool_log, usage


def investigate(*a, **kw) -> tuple[str, list[dict], dict[str, Any]]:
    return asyncio.run(investigate_async(*a, **kw))
