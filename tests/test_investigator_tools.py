"""Catalogue #42: a read-only tool that raises kills the agent's turn.

Asking a fleet-wide question ("which jobs needed a human?") gave the model no job to name, so it called a tool with an
empty job id. That reached Firestore as the document path `runs/`, which answers 400, and the request hung until it
timed out. A tool must answer with a recoverable error and tell the agent how to find the job instead.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")

from warden.agents import investigator as I  # noqa: E402
from warden.core.models import Job, JobStatus  # noqa: E402
from warden.store import firestore as db  # noqa: E402

JOB_TOOLS = [I.get_log_window, I.search_log, I.get_heartbeats, I.get_artifacts, I.get_incident_history]
ARGS = {"get_log_window": {"start_line": 1, "end_line": 5}, "search_log": {"pattern": "x"}}


@pytest.fixture(autouse=True)
def fresh():
    for coll in ("jobs", "incidents"):
        for d in db.client().collection(coll).limit(200).stream():
            d.reference.delete()
    db.jobs.put(Job(job_id="known-job", status=JobStatus.RUNNING))
    yield


@pytest.mark.parametrize("tool", JOB_TOOLS, ids=[t.__name__ for t in JOB_TOOLS])
@pytest.mark.parametrize("job_id", ["", "   ", "no-such-job"])
def test_a_tool_never_raises_on_a_job_it_cannot_read(tool, job_id):
    out = tool(job_id, **ARGS.get(tool.__name__, {}))
    assert isinstance(out, dict) and out.get("error"), f"{tool.__name__} must return an error, not raise"
    assert "jobs" in out, "the error must tell the agent which jobs exist"


def test_discovery_tools_exist_for_a_question_that_names_no_job():
    names = [t.__name__ for t in I.TOOLS]
    assert "list_jobs" in names and "list_incidents" in names
    assert any(j["job_id"] == "known-job" for j in I.list_jobs()["jobs"])
