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

    monkeypatch.setattr(dc.httpx, "patch", lambda url, **kw: (posted.update({"url": url, "json": kw.get("json")}), R())[1])
    monkeypatch.setattr("warden.agents.concierge.ask", lambda *a, **kw: {"answer": "The disk filled up.", "ok": True})

    out = dc.answer_pending_asks()
    assert out["answered"] == 1 and out["failed"] == 0
    assert posted["url"].endswith("/webhooks/app/tok/messages/@original"), \
        "editing the deferred reply is what keeps the question visible above the answer"
    assert "The disk filled up." in posted["json"]["content"]
    assert not db.client().collection("discord_asks").document("I1").get().exists, "an answered question is cleared"


def test_a_failed_answer_still_replies(monkeypatch):
    dc.handle_interaction(_interaction("boom"))
    posted = {}

    class R:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(dc.httpx, "patch", lambda url, **kw: (posted.update({"json": kw.get("json")}), R())[1])

    def boom(*a, **kw):
        raise RuntimeError("Gemini unavailable")

    monkeypatch.setattr("warden.agents.concierge.ask", boom)
    out = dc.answer_pending_asks()
    assert out["failed"] == 1
    assert "Could not answer" in posted["json"]["content"], "silence on a phone is worse than an error"


def test_a_button_press_is_acknowledged_before_the_work_starts():
    """Approving calls Compute Engine, which outlives Discord's three-second window.

    The button showed "WARDEN didn't respond in time" while the disk had already grown to 30 GB — the action was fine,
    the acknowledgement was late. Type 6 keeps the card and marks it working; the tick does the work.
    """
    for d in db.client().collection("discord_actions").limit(50).stream():
        d.reference.delete()
    out = dc.handle_interaction({
        "id": "B1", "token": "btok", "application_id": "app", "type": 3,
        "member": {"user": {"id": "u1", "username": "owner"}},
        "data": {"custom_id": "warden:approve:dec_X"}})
    assert out == {"type": 6}, "Discord must be answered before Compute Engine is called"
    rec = db.client().collection("discord_actions").document("B1").get().to_dict()
    assert rec["verb"] == "approve" and rec["decision_id"] == "dec_X" and rec["state"] == "pending"


def test_the_tick_runs_the_press_and_edits_the_card(monkeypatch):
    for d in db.client().collection("discord_actions").limit(50).stream():
        d.reference.delete()
    dc.handle_interaction({"id": "B2", "token": "btok", "application_id": "app", "type": 3,
                           "member": {"user": {"id": "u1", "username": "owner"}},
                           "data": {"custom_id": "warden:approve:dec_Y"}})
    posted = {}

    class R:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(dc.httpx, "patch", lambda url, **kw: (posted.update({"url": url, "json": kw.get("json")}), R())[1])
    monkeypatch.setattr(dc.approvals, "approve", lambda did, who: {"ok": True, "observed": "30 GB"})

    out = dc.run_pending_actions()
    assert out["done"] == 1
    assert "30 GB" in posted["json"]["content"] and "owner" in posted["json"]["content"]
    assert posted["json"]["components"] == [], "a decided card must not keep its buttons"


def test_an_unknown_button_is_refused_without_parking_work():
    out = dc.handle_interaction({"id": "B3", "token": "t", "application_id": "app", "type": 3,
                                 "member": {"user": {"id": "u1", "username": "owner"}},
                                 "data": {"custom_id": "warden:destroy:dec_Z"}})
    assert out["type"] == 4 and "unknown" in out["data"]["content"]
    assert not db.client().collection("discord_actions").document("B3").get().exists
