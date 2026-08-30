"""An approval that lapses must speak. Silence is the worst outcome for an on-call human.

Warden did not act, and the person never learned they were needed — while whatever the action would have stopped keeps
running and keeps costing. This pins that expiry notifies, says what it would have done, and offers a way back.
"""
from __future__ import annotations

import os
from datetime import timedelta

import pytest

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")

from warden.concierge import discord as dc  # noqa: E402
from warden.core.models import (Action, Decision, DecisionStatus, Incident, IncidentState, Verdict, now)  # noqa: E402
from warden.executor import approvals  # noqa: E402
from warden.store import firestore as db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    for coll in ("incidents", "decisions"):
        for d in db.client().collection(coll).limit(200).stream():
            d.reference.delete()
    yield


def _waiting(burn: float = 0.05):
    inc = Incident(job_id="vision-7b", rule="orphan", severity="warning", summary="machine with no job",
                   state=IncidentState.AWAITING_APPROVAL, cost_burning_usd_per_hour=burn)
    db.incidents.put(inc)
    dec = Decision(incident_id=inc.incident_id, job_id="vision-7b", action=Action.STOP_INSTANCE,
                   verdict=Verdict.NEED_APPROVAL, status=DecisionStatus.PENDING,
                   expires_at=now() - timedelta(minutes=1))
    db.decisions.put(dec)
    inc.decision_ids.append(dec.decision_id); db.incidents.put(inc)
    return inc, dec


def test_expiry_tells_the_human_what_was_missed_and_what_it_costs():
    inc, dec = _waiting(burn=0.05)
    seen = []
    n = approvals.expire_stale(notify=lambda i, d, t: seen.append(t))
    assert n == 1
    assert db.decisions.get(dec.decision_id).status == DecisionStatus.EXPIRED
    assert db.incidents.get(inc.incident_id).state == IncidentState.ESCALATED
    assert seen, "an approval that lapses in silence is the failure this guards against"
    msg = seen[0]
    assert "stop instance" in msg and "vision-7b" in msg
    assert "$1.20 a day" in msg, "the cost of waiting is the reason to answer"
    assert "Re-evaluate" in msg


def test_a_free_incident_does_not_invent_a_cost():
    _waiting(burn=0.0)
    seen = []
    approvals.expire_stale(notify=lambda i, d, t: seen.append(t))
    assert "a day" not in seen[0]


def test_the_expired_card_still_offers_a_way_back():
    inc, dec = _waiting()
    approvals.expire_stale(notify=lambda *a: None)
    card = dc.build_card(db.incidents.get(inc.incident_id), db.decisions.get(dec.decision_id), "expired")
    ids = [c["custom_id"] for row in card.get("components", []) for c in row["components"]]
    assert any(i.startswith("warden:reevaluate:") for i in ids), "the phone must be able to act on it"
    assert not any(i.startswith("warden:approve:") for i in ids), "an expired decision cannot be approved"
