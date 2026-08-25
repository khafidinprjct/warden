"""Insiden 25 Agu: denyut agen membawa run_id LAMA (train.json basi) menimpa run_id baru → RUN_FIN exit 1 run baru tak terlihat."""
import os
os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081"); os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test"); os.environ.setdefault("WARDEN_PROVIDER", "fake")
from datetime import timedelta
from warden.core.models import Job, JobStatus, Marker, now
from warden.signals.ingest import ingest_heartbeat
from warden.store import firestore as db


def _reset():
    for coll in ("jobs", "markers", "runs"):
        for d in db.client().collection(coll).limit(300).stream():
            if coll == "runs":
                for h in d.reference.collection("heartbeats").limit(500).stream():
                    h.reference.delete()
            d.reference.delete()
    for c in db.client().collection("jobs").document("j1").collection("heartbeats").limit(300).stream():
        c.reference.delete()


def _hb(run_id, step):
    return {"job_id": "j1", "run_id": run_id, "ts": now().isoformat(), "phase": "train", "step": step, "loss": 0.1}


def test_stale_heartbeat_cannot_move_run_id_backwards():
    _reset()
    db.jobs.put(Job(job_id="j1", status=JobStatus.RUNNING, run_id="r1"))
    db.put_marker(Marker(job_id="j1", run_id="r1", kind="RUN_START", ts=now() - timedelta(hours=1), valid=True))
    db.put_marker(Marker(job_id="j1", run_id="r2", kind="RUN_START", ts=now(), valid=True))
    ingest_heartbeat(_hb("r2", 5)); j = db.jobs.get("j1")
    assert j.run_id == "r2" and j.last_step == 5                      # run baru (RUN_START lebih baru) diterima
    ingest_heartbeat(_hb("r1", 1700)); j = db.jobs.get("j1")
    assert j.run_id == "r2" and j.last_step == 5                      # denyut basi run lama DIABAIKAN seluruhnya
    ingest_heartbeat(_hb("r3", 9)); j = db.jobs.get("j1")
    assert j.run_id == "r2"                                           # run tanpa RUN_START tidak menimpa run yang dikenal


def test_marker_run_start_still_moves_run_id():
    _reset()
    db.jobs.put(Job(job_id="j1", status=JobStatus.RUNNING, run_id="r1"))
    from warden.signals.ingest import _touch_job
    _touch_job("j1", run_id="r9", phase="train")                      # sumber marker (default) selalu boleh
    assert db.jobs.get("j1").run_id == "r9"
