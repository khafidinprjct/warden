"""Concierge (Phase A-3): answers an operator's question about a job or incident with the same read-only tools as the Investigator
plus incident memory. Never acts. Used by the dashboard (/ask) and later by Discord (/warden why)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from warden.agents import memory
from warden.agents.investigator import PRICE, TOOLS
from warden.config import settings
from warden.store import firestore as db

SYSTEM = (
    "You are the concierge of Warden, an SRE system for long-running compute jobs. Answer the operator's question about a job, "
    "an incident or the fleet using the read-only tools and the context given. Be concrete: cite log line numbers, timestamps, "
    "byte counts, decision ids. If you do not know, say so and name the tool result that was missing. Use as many tool calls as needed. "
    "An incident summary describes the moment the incident was opened, not the present: it is history. Before saying anything "
    "about what a machine is doing now — running, stopped, what it costs per hour — call list_fleet and use that. Quoting an old "
    "summary in the present tense is the mistake to avoid. "
    "You cannot start, stop or change anything; if the operator asks for an action, name the Warden decision that would do it and "
    "say it needs their approval — they can approve it from the card in this channel. Answer in English, ≤ 180 words, "
    "plain product vocabulary."
)


def _context(job_id: str, incident_id: str) -> dict:
    ctx: dict[str, Any] = {}
    if job_id:
        j = db.jobs.get(job_id)
        if j:
            ctx["job"] = {"job_id": j.job_id, "status": str(j.status), "phase": j.phase, "run_id": j.run_id, "last_step": j.last_step, "legacy": j.legacy}
        ctx["postmortems"] = memory.recall(job_id=job_id, n=3)
    if incident_id:
        i = db.incidents.get(incident_id)
        if i:
            ctx["incident"] = {"incident_id": i.incident_id, "rule": i.rule, "state": str(i.state), "summary": i.summary, "diagnosis": i.diagnosis, "decisions": i.decision_ids}
    ctx["health"] = {d.id: (d.to_dict() or {}).get("ok") for d in db.client().collection("health").stream()}
    return ctx


async def ask_async(question: str, job_id: str = "", incident_id: str = "", model: str | None = None, image: bytes | None = None, image_mime: str = "image/png") -> dict:
    agent = LlmAgent(name="concierge", model=model or settings.gemini_model, instruction=SYSTEM, tools=TOOLS, output_key="answer",
                     generate_content_config=types.GenerateContentConfig(temperature=0.2))
    runner = InMemoryRunner(agent=agent, app_name="warden")
    session = await runner.session_service.create_session(app_name="warden", user_id="operator")
    ctx = _context(job_id, incident_id)
    parts = [types.Part(text="## context\n" + json.dumps(ctx, ensure_ascii=False, default=str)[:6000] + "\n\n## question\n" + question[:1000])]
    if image:
        parts.append(types.Part(text="## attached image (a screenshot or photo from the operator's phone — read it, quote what you can see, label every finding "
                                     "'from the image' with a confidence ≤ 0.6, and never treat it as ground truth over Warden's own data)"))
        parts.append(types.Part.from_bytes(data=image, mime_type=image_mime))
    msg = types.Content(role="user", parts=parts)
    usage = {"prompt_tokens": 0, "output_tokens": 0}; tools_used: list[str] = []; answer = ""
    async for ev in runner.run_async(user_id="operator", session_id=session.id, new_message=msg):
        if getattr(ev, "usage_metadata", None):
            usage["prompt_tokens"] += getattr(ev.usage_metadata, "prompt_token_count", 0) or 0
            usage["output_tokens"] += getattr(ev.usage_metadata, "candidates_token_count", 0) or 0
        for fc in ev.get_function_calls() or []:
            tools_used.append(fc.name)
        if ev.is_final_response() and ev.content and ev.content.parts:
            answer = "".join(p.text or "" for p in ev.content.parts)
    pin, pout = PRICE.get(model or settings.gemini_model, (1.5, 9.0))
    cost = round(usage["prompt_tokens"] / 1e6 * pin + usage["output_tokens"] / 1e6 * pout, 6)
    from warden.core.models import now
    db.cost_add(now().strftime("%Y-%m-%d"), "llm_usd", cost, job_id or "concierge")
    return {"ok": True, "answer": answer.strip(), "tools_used": tools_used, "cost_usd": cost, "model": model or settings.gemini_model, "image": bool(image)}


def ask(*a, **kw) -> dict:
    return asyncio.run(ask_async(*a, **kw))
