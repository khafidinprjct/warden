"""A request that reaches AWAITING_APPROVAL must reach a human too.

`propose` is the door the dashboard and Ask Warden use. It went through policy correctly and parked the decision in
Firestore correctly — and then told nobody. The person allowed to approve is on their phone, so a proposal with no
card sent is a proposal that waits until it expires. This pins the announcement for every verdict propose can reach.
"""
from __future__ import annotations

import os

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")

from warden.core.models import IncidentState, Job, JobStatus  # noqa: E402
from warden.executor import approvals  # noqa: E402
from warden.providers import registry  # noqa: E402
from warden.store import firestore as db  # noqa: E402


def _reset():
    registry._fake = None
    for coll in ("fleet", "jobs", "incidents", "decisions", "audit", "policies", "policy_overrides"):
        for d in db.client().collection(coll).limit(300).stream():
            d.reference.delete()


def _job(legacy: bool = True) -> str:
    fake = registry.compute(); inst = fake.add("prop1")
    db.jobs.put(Job(job_id="jp", instance_ref=inst.ref, status=JobStatus.RUNNING, legacy=legacy))
    return "jp"


def test_proposal_needing_approval_sends_a_card():
    _reset(); job_id = _job(legacy=True)
    seen: list[tuple] = []
    r = approvals.propose(job_id, "stop_instance", {}, "dashboard", "machine looks idle",
                          notify=lambda inc, dec, text: seen.append((inc, dec, text)))
    assert r["verdict"] == "NEED_APPROVAL", r
    assert db.incidents.get(r["incident_id"]).state == IncidentState.AWAITING_APPROVAL
    assert seen, "a decision parked in AWAITING_APPROVAL was never announced"
    inc, dec, text = seen[-1]
    assert dec.decision_id == r["decision_id"]
    assert "approval required" in text and "Stop instance" in text and job_id in text
    assert "machine looks idle" in text


def test_proposal_executed_straight_away_is_still_reported():
    _reset(); job_id = _job(legacy=False)
    seen: list[str] = []
    r = approvals.propose(job_id, "notify", {}, "dashboard", "", notify=lambda inc, dec, text: seen.append(text))
    assert r["verdict"] == "AUTO", r
    assert seen and "Notify" in seen[-1]


def test_propose_without_a_notifier_still_works():
    _reset(); job_id = _job(legacy=True)
    r = approvals.propose(job_id, "stop_instance", {}, "dashboard")
    assert r["verdict"] == "NEED_APPROVAL" and r["ok"]
