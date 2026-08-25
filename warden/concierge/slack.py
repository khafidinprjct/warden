"""Adapter Slack (Fase 13, kanal kedua): kontrak sama dengan Discord — kartu + tombol approve/deny/always,
verifikasi tanda tangan v0 (HMAC-SHA256 atas 'v0:ts:body', timestamp ≤ 5 mnt). Aktif bila WARDEN_SLACK_* diisi."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

import httpx

from warden.core.models import Decision, Incident
from warden.executor import approvals
from warden.store import firestore as db

SIGNING = os.getenv("WARDEN_SLACK_SIGNING_SECRET", ""); TOKEN = os.getenv("WARDEN_SLACK_BOT_TOKEN", ""); CHANNEL = os.getenv("WARDEN_SLACK_CHANNEL", "")


def verify(ts: str, body: bytes, sig: str) -> bool:
    if not SIGNING or abs(time.time() - float(ts or 0)) > 300:
        return False
    base = f"v0:{ts}:".encode() + body
    return hmac.compare_digest("v0=" + hmac.new(SIGNING.encode(), base, hashlib.sha256).hexdigest(), sig or "")


def blocks(inc: Incident | None, dec: Decision | None, text: str) -> list[dict[str, Any]]:
    b: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}]
    if inc:
        b.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"{inc.incident_id} · {inc.rule} · {inc.state} · bakar ${inc.cost_burning_usd_per_hour:.3f}/jam"}]})
    if dec and dec.verdict == "NEED_APPROVAL" and dec.status == "PENDING":
        b.append({"type": "actions", "elements": [
            {"type": "button", "style": "primary", "text": {"type": "plain_text", "text": "Approve"}, "action_id": "warden:approve", "value": dec.decision_id},
            {"type": "button", "style": "danger", "text": {"type": "plain_text", "text": "Deny"}, "action_id": "warden:deny", "value": dec.decision_id},
            {"type": "button", "text": {"type": "plain_text", "text": "Always 24h"}, "action_id": "warden:always", "value": dec.decision_id}]})
    return b


def send(inc: Incident | None, dec: Decision | None, text: str) -> None:
    if not TOKEN or not CHANNEL:
        return
    try:
        r = httpx.post("https://slack.com/api/chat.postMessage", headers={"Authorization": f"Bearer {TOKEN}"},
                       json={"channel": CHANNEL, "text": text[:200], "blocks": blocks(inc, dec, text)}, timeout=10).json()
        db.health("slack", bool(r.get("ok")), str(r.get("error", ""))[:100])
    except Exception as e:
        db.health("slack", False, str(e)[:200])


def handle_action(payload: dict[str, Any]) -> dict[str, Any]:
    """Interaktivitas Slack: ack ≤3 s (respons ini), keputusan lewat approvals (Firestore)."""
    act = (payload.get("actions") or [{}])[0]; verb = act.get("action_id", "").split(":")[-1]; did = act.get("value", "")
    who = f"slack:{(payload.get('user') or {}).get('username', '?')}"
    r = approvals.approve(did, who) if verb in ("approve", "always") else approvals.deny(did, who) if verb == "deny" else {"ok": False, "error": "verb"}
    return {"replace_original": True, "text": f"{'✅ disetujui' if r.get('ok') and verb != 'deny' else '🚫 ditolak' if verb == 'deny' else '❌ ' + str(r.get('error'))} oleh {who} · {r.get('observed', '')}"}
