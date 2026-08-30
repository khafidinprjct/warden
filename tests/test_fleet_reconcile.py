"""A machine deleted outside Warden must stop being reported as running.

The ledger row is written from the provider listing, so a deleted instance simply stopped being updated and kept its last
status forever — production listed `warden-live-1539-phase` as RUNNING after the drill had deleted it. The row is kept
(it is evidence); only its status is reconciled, and only when the listing can be trusted.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")

from warden.providers import registry  # noqa: E402
from warden.store import firestore as db  # noqa: E402
from warden.watcher import tick as T  # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    registry._fake = None
    T._prev_status.clear()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "notifications"):
        for d in db.client().collection(coll).limit(200).stream():
            if coll == "runs":
                for h in d.reference.collection("heartbeats").limit(500).stream():
                    h.reference.delete()
            d.reference.delete()
    yield


def _refs():
    return {i.ref: str(i.status) for i in db.fleet.list(limit=50)}


def test_deleted_machine_is_marked_gone_and_the_row_is_kept():
    fake = registry.compute()
    a = fake.add("warden-a"); fake.add("warden-b")
    T.run_tick()
    assert _refs()[a.ref] == "RUNNING"

    del fake.instances[a.ref]                      # deleted at the provider, as `gcloud compute instances delete` does
    T.run_tick()
    after = _refs()
    assert after[a.ref] == "DELETED", "a machine that no longer exists must not stay RUNNING"
    assert "warden-b" in " ".join(after), "the surviving machine is untouched"
    assert len(after) == 2, "the ledger row is kept as evidence, never removed"


def test_a_compute_api_failure_does_not_mark_the_whole_fleet_gone(monkeypatch):
    fake = registry.compute()
    a = fake.add("warden-a")
    T.run_tick()
    assert _refs()[a.ref] == "RUNNING"

    def boom():
        raise RuntimeError("compute API unavailable")
    monkeypatch.setattr(type(fake), "list_instances", lambda self: boom())
    T.run_tick()
    assert _refs()[a.ref] == "RUNNING", "an empty listing caused by an API error is not evidence of deletion"
