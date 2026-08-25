"""Diagnostician: agen LLM ADK dengan output_schema=Diagnosis. LLM tidak memegang tombol (P1).
Dipanggil hanya untuk temuan yang butuh pemahaman teks (needs_llm)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from warden.agents.schemas import Diagnosis
from warden.config import settings

SYSTEM = (
    "Kamu SRE untuk pekerjaan komputasi jangka panjang (training/eval/pipeline) di mesin cloud. "
    "Diagnosis HANYA dari bukti yang diberikan. Setiap klaim wajib menunjuk evidence_lines (nomor baris log_tail) "
    "dan evidence_quotes harus kutipan persis dari baris itu. Bedakan proses yang KEHABISAN memori dari frame yang "
    "MENGHABISKAN (culprit_frame). Bila bukti tidak cukup, pakai category=unknown dan needs_human=true — jangan menebak. "
    "recommended_action hanya dari daftar; tidak ada tindakan destruktif (delete/overwrite). "
    "human_summary_id dalam Bahasa Indonesia, ≤280 karakter, sebutkan biaya bila ada di job_card. "
    "falsifiable_check: kalimat 'kalau diagnosis benar, setelah tindakan X angka Y berubah'."
)


def build_agent(model: str | None = None) -> LlmAgent:
    return LlmAgent(name="diagnostician", model=model or settings.gemini_model, instruction=SYSTEM,
                    output_schema=Diagnosis, output_key="diagnosis",
                    generate_content_config=types.GenerateContentConfig(temperature=0.1))


def _pack(job_card: dict, findings: list[dict], hb_summary: dict, log_lines: list[str], dmesg: list[str] | None) -> str:
    numbered = "\n".join(f"{i+1:4d}| {l[:300]}" for i, l in enumerate(log_lines))
    parts = [
        "## job_card\n" + json.dumps(job_card, ensure_ascii=False)[:1500],
        "## deterministic_findings\n" + json.dumps(findings, ensure_ascii=False)[:1500],
        "## heartbeat_summary\n" + json.dumps(hb_summary, ensure_ascii=False)[:1500],
        "## log_tail (nomor baris | isi)\n" + numbered,
    ]
    if dmesg:
        parts.append("## dmesg_tail\n" + "\n".join(dmesg[-30:]))
    return "\n\n".join(parts)


async def diagnose_async(job_card: dict, findings: list[dict], hb_summary: dict, log_lines: list[str],
                         dmesg: list[str] | None = None, model: str | None = None,
                         image_png: bytes | None = None) -> tuple[Diagnosis, dict[str, Any]]:
    agent = build_agent(model)
    runner = InMemoryRunner(agent=agent, app_name="warden")
    session = await runner.session_service.create_session(app_name="warden", user_id="warden")
    parts = [types.Part(text=_pack(job_card, findings, hb_summary, log_lines, dmesg))]
    if image_png:
        parts.append(types.Part.from_bytes(data=image_png, mime_type="image/png"))
    msg = types.Content(role="user", parts=parts)
    usage: dict[str, Any] = {"prompt_tokens": 0, "output_tokens": 0}
    final_text = ""
    async for ev in runner.run_async(user_id="warden", session_id=session.id, new_message=msg):
        if getattr(ev, "usage_metadata", None):
            usage["prompt_tokens"] += getattr(ev.usage_metadata, "prompt_token_count", 0) or 0
            usage["output_tokens"] += getattr(ev.usage_metadata, "candidates_token_count", 0) or 0
        if ev.is_final_response() and ev.content and ev.content.parts:
            final_text = "".join(p.text or "" for p in ev.content.parts)
    diag = Diagnosis.model_validate_json(final_text)
    # harga per 1M token (Agu 2026): flash $1,50/$9 ; flash-lite $0,30/$2,50 ; 3.7 flash $0,75/$3,75
    price = {"gemini-3.5-flash": (1.5, 9.0), "gemini-3.5-flash-lite": (0.3, 2.5), "gemini-3.7-flash": (0.75, 3.75)}
    pin, pout = price.get(model or settings.gemini_model, (1.5, 9.0))
    usage["cost_usd"] = round(usage["prompt_tokens"] / 1e6 * pin + usage["output_tokens"] / 1e6 * pout, 6)
    usage["model"] = model or settings.gemini_model
    return diag, usage


def diagnose(*a, **kw) -> tuple[Diagnosis, dict[str, Any]]:
    return asyncio.run(diagnose_async(*a, **kw))
