"""`/warden ask` must work from Discord itself, or the phone channel is only a noticeboard.

Discord requires a reply within three seconds; the Concierge needs ten to thirty. So the interaction is acknowledged
immediately and the question parked, then the tick answers it and posts a follow-up. These tests pin both halves,
including the part that is easy to get wrong: the attachment arrives as an id that has to be resolved to a URL.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")
os.environ.setdefault("WARDEN_DEV", "1")

from warden.concierge import discord as dc  # noqa: E402
from warden.store import firestore as db  # noqa: E402


def _interaction(question: str, attachment_url: str | None = None) -> dict:
    data = {"name": "warden", "options": [{"name": "ask", "options": [{"name": "question", "value": question}]}]}
    if attachment_url:
        data["options"][0]["options"].append({"name": "image", "value": "A1"})
        data["resolved"] = {"attachments": {"A1": {"url": attachment_url}}}
    return {"id": "I1", "token": "tok", "application_id": "app", "type": 2,
            "member": {"user": {"id": "u1", "username": "owner"}}, "data": data}


@pytest.fixture(autouse=True)
def fresh():
    for d in db.client().collection("discord_asks").limit(50).stream():
        d.reference.delete()
    yield


def test_ask_is_acknowledged_immediately_and_parked():
    out = dc.handle_interaction(_interaction("why did the job stop?"))
    assert out == {"type": 5}, "Discord must be answered inside three seconds"
    rec = db.client().collection("discord_asks").document("I1").get().to_dict()
    assert rec["question"] == "why did the job stop?" and rec["state"] == "pending"
    assert rec["token"] == "tok" and rec["application_id"] == "app"


def test_an_attachment_id_is_resolved_to_its_url():
    dc.handle_interaction(_interaction("what is this?", "https://cdn.example/x.png"))
    rec = db.client().collection("discord_asks").document("I1").get().to_dict()
    assert rec["image_url"] == "https://cdn.example/x.png"


def test_the_tick_answers_and_posts_a_follow_up(monkeypatch):
    dc.handle_interaction(_interaction("why did the job stop?"))
    posted = {}

    class R:
        status_code = 200
        content = b"\x89PNG"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(dc.httpx, "post", lambda url, **kw: (posted.update({"url": url, "json": kw.get("json")}), R())[1])
    monkeypatch.setattr("warden.agents.concierge.ask", lambda *a, **kw: {"answer": "The disk filled up.", "ok": True})

    out = dc.answer_pending_asks()
    assert out["answered"] == 1 and out["failed"] == 0
    assert "/webhooks/app/tok" in posted["url"], "the follow-up goes to the interaction webhook"
    assert "The disk filled up." in posted["json"]["content"]
    assert not db.client().collection("discord_asks").document("I1").get().exists, "an answered question is cleared"


def test_a_failed_answer_still_replies(monkeypatch):
    dc.handle_interaction(_interaction("boom"))
    posted = {}

    class R:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(dc.httpx, "post", lambda url, **kw: (posted.update({"json": kw.get("json")}), R())[1])

    def boom(*a, **kw):
        raise RuntimeError("Gemini unavailable")

    monkeypatch.setattr("warden.agents.concierge.ask", boom)
    out = dc.answer_pending_asks()
    assert out["failed"] == 1
    assert "Could not answer" in posted["json"]["content"], "silence on a phone is worse than an error"
