"""The Gemini circuit breaker must record closing, not only opening."""
import os
os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")
from warden.agents import pipeline
from warden.store import firestore as db


def _row(k):
    d = db.client().collection("health").document(k).get()
    return d.to_dict() if d.exists else {}


def test_breaker_opens_then_closes():
    for _ in range(5):
        db.health("gemini", False, "boom")
    pipeline.process_diagnosing()
    assert _row("llm_circuit").get("ok") is False, "five failures in a row must open the breaker"

    db.health("gemini", True)                      # one success resets the streak
    pipeline.process_diagnosing()
    assert _row("llm_circuit").get("ok") is True, "a closed breaker must be recorded, not left red for ever"
