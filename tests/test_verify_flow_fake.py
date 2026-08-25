import os, time
os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081"); os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test"); os.environ.setdefault("WARDEN_PROVIDER", "fake")
from pathlib import Path
from warden.core.models import IncidentState, Job, JobStatus, Marker, now
from warden.providers import registry
from warden.signals.ingest import sign
from warden.store import firestore as db
from warden.verifier.run import process_pending
from warden.watcher import tick as T


def _reset():
    registry._fake = None; T._prev_status.clear()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "notifications", "policies"):
        for d in db.client().collection(coll).limit(300).stream():
            if coll == "runs":
                for h in d.reference.collection("heartbeats").limit(500).stream():
                    h.reference.delete()
            d.reference.delete()


def _fin(job, name, path, exit_code=0):
    ts = now()
    sig = sign(f"{job.job_id}|{job.run_id}|{exit_code}|{ts.isoformat()}".encode())
    from warden.signals.ingest import validate_marker
    import hashlib
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    mk = validate_marker(Marker(job_id=job.job_id, run_id=job.run_id, kind="RUN_FIN", ts=ts, exit_code=exit_code, signature=sig,
                                artifacts=[{"path": str(path), "bytes": path.stat().st_size, "sha256": sha}]))
    assert mk.valid, mk.invalid_reason
    db.put_marker(mk)


def test_fin_ok_verified_to_complete(tmp_path, monkeypatch):
    _reset(); monkeypatch.chdir(tmp_path)
    fake = registry.compute(); inst = fake.add("vm-v")
    d = Path("data/gcs/jv/artifacts"); d.mkdir(parents=True)
    p = d / "pred.csv"; p.write_text("ID,TargetF1,TargetRAUC\n" + "".join(f"{i},1,0.4\n" for i in range(5))); os.utime(p, (time.time()-600,)*2)
    job = Job(job_id="jv", instance_ref=inst.ref, status=JobStatus.RUNNING, run_id="r1", phase="F6", command="/venv/bin/python x.py",
              expect={"pred.csv": {"rows": 5, "columns": ["ID", "TargetF1", "TargetRAUC"], "range01_columns": ["TargetRAUC"]}})
    db.jobs.put(job); _fin(job, "pred.csv", p)
    T.run_tick(); r = process_pending()
    assert r["ok"] == 1, r
    assert db.jobs.get("jv").status == JobStatus.COMPLETE
    assert db.get_marker("jv", "r1", "VERIFIED") is not None


def test_fin_ok_but_corrupt_is_rejected(tmp_path, monkeypatch):
    _reset(); monkeypatch.chdir(tmp_path)
    fake = registry.compute(); inst = fake.add("vm-c")
    d = Path("data/gcs/jc/artifacts"); d.mkdir(parents=True)
    p = d / "pred.csv"; p.write_text("ID,TargetF1,TargetRAUC\n1,1,nan\n2,0,0.3\n"); os.utime(p, (time.time()-600,)*2)
    job = Job(job_id="jc", instance_ref=inst.ref, status=JobStatus.RUNNING, run_id="r1", phase="F6", command="/venv/bin/python x.py",
              expect={"pred.csv": {"rows": 5}})
    db.jobs.put(job); _fin(job, "pred.csv", p)
    T.run_tick(); r = process_pending()
    assert r["ok"] == 0
    assert db.jobs.get("jc").status == JobStatus.FINISHED_UNVERIFIED
    inc = [i for i in db.incidents.list(job_id="jc") if i.rule == "artifact_unverified"][0]
    assert "finished ≠ intact" in inc.summary and inc.state in (IncidentState.ESCALATED, IncidentState.AWAITING_APPROVAL)
    cmd = db.client().collection("cmd").document("jc").get().to_dict()   # karantina otomatis (L2) via signed mailbox
    assert cmd and cmd["cmd"] == "quarantine" and cmd["args"]["path"] == "pred.csv" and cmd["sig"]
