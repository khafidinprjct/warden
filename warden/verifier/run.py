"""Menjalankan Verifier untuk insiden fin_ok_pending_verify: unduh artefak dari GCS (atau lokal), buka, putuskan.
Lulus → VERIFIED marker + job COMPLETE + last_good; gagal → insiden artifact_unverified + karantina (L2) / rollback (L1)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from warden.config import settings
from warden.core.models import Action, DecisionStatus, Evidence, IncidentState as S, JobStatus, Marker, Verdict, now
from warden.core.state_machine import transition
from warden.executor import registry as ex
from warden.policy.engine import evaluate as policy_eval, load_policy
from warden.providers.registry import compute
from warden.store import firestore as db
from warden.verifier.base import verify
from warden.watcher.tick import _ctx_for, _is_frozen

POLICY = load_policy()


def _fetch(job_id: str, name: str) -> Path | None:
    if settings.bucket:
        try:
            from google.cloud import storage
            blob = storage.Client().bucket(settings.bucket).blob(f"jobs/{job_id}/artifacts/{name}")
            if blob.exists():
                d = Path(tempfile.mkdtemp()); p = d / name; blob.download_to_filename(str(p))
                import os, time
                os.utime(p, (time.time() - 600, time.time() - 600))   # sudah diam di sisi mesin
                return p
        except Exception as e:
            db.health("gcs", False, str(e)[:200])
    p = Path("data/gcs") / job_id / "artifacts" / name
    return p if p.exists() else None


def verify_incident(inc, notify=None) -> dict[str, Any]:
    job = db.jobs.get(inc.job_id); inst = compute().describe(inc.instance_ref) if inc.instance_ref else None
    fin = db.get_marker(inc.job_id, job.run_id if job else "", "RUN_FIN") if job else None
    results: list[dict] = []; all_ok = True; missing = 0
    expect_map = (job.expect if job else {}) or {}
    for a in (fin.artifacts if fin else []):
        name = Path(a["path"]).name
        p = _fetch(inc.job_id, name)
        if p is None:
            missing += 1; results.append({"name": name, "ok": False, "reason": "artefak tidak tersedia untuk diverifikasi"}); all_ok = False; continue
        exp = expect_map.get(name) or expect_map.get(Path(name).suffix.lstrip(".")) or {}
        r = verify(p, exp, declared_sha256=a.get("sha256", ""), prev_sha256=(job.last_good_ckpt or {}).get("sha256", "") if name.endswith((".pt", ".pth", ".ckpt")) else "")
        results.append({"name": name, "ok": r.ok, "reason": r.corrupt_reason, "checks": r.checks, "bytes": r.bytes, "sha256": r.sha256})
        all_ok = all_ok and r.ok
    ev = Evidence(incident_id=inc.incident_id, kind="artifact_check", summary=f"{sum(1 for x in results if x['ok'])}/{len(results)} artefak lolos", payload={"results": results})
    db.evidence.put(ev); inc.evidence_ids.append(ev.evidence_id)
    if all_ok and results:
        db.put_marker(Marker(job_id=inc.job_id, run_id=job.run_id, kind="VERIFIED", valid=True, artifacts=[{"name": x["name"], "sha256": x["sha256"], "bytes": x["bytes"]} for x in results]))
        job.status = JobStatus.COMPLETE
        ck = [x for x in results if x["name"].endswith((".pt", ".pth", ".ckpt"))]
        if ck:
            job.last_good_ckpt = {"path": ck[-1]["name"], "sha256": ck[-1]["sha256"], "step": job.last_step}
        db.jobs.put(job)
        transition(inc, S.DECIDED, note="artefak terbuka & utuh"); transition(inc, S.RESOLVED, note="VERIFIED ditulis"); db.incidents.put(inc)
        if notify: notify(inc, None, f"✅ {inc.job_id}: {len(results)} artefak dibuka & utuh → COMPLETE (VERIFIED)")
        return {"ok": True, "results": results}
    # gagal: job tetap FINISHED_UNVERIFIED; karantina otomatis (L2) artefak yang rusak
    job.status = JobStatus.FINISHED_UNVERIFIED; db.jobs.put(job)
    bad = [x for x in results if not x["ok"]]
    inc.rule = "artifact_unverified"; inc.severity = "critical"
    inc.summary = f"{inc.job_id}: selesai ≠ utuh — {len(bad)}/{len(results)} artefak gagal: " + "; ".join(f"{x['name']}: {x['reason']}" for x in bad)[:300]
    action = Action.QUARANTINE_ARTIFACT
    dec = policy_eval(action, _ctx_for(job, inst, action, _is_frozen()), POLICY)
    dec.incident_id = inc.incident_id; dec.params = {"instance_ref": inc.instance_ref, "path": bad[0]["name"] if bad else ""}
    dec.dry_run_plan = ex.dry_run(dec, compute()); db.decisions.put(dec); inc.decision_ids.append(dec.decision_id)
    transition(inc, S.DECIDED, note=f"quarantine: {dec.verdict}")
    if dec.verdict == Verdict.AUTO and inst:
        transition(inc, S.EXECUTING); dec.status = DecisionStatus.EXECUTING; db.decisions.put(dec)
        r = ex.execute(dec, compute()); dec.status = DecisionStatus.DONE if r.ok else DecisionStatus.FAILED
        transition(inc, S.VERIFYING if r.ok else S.FAILED_ACTION); transition(inc, S.ESCALATED, note="artefak dikarantina; butuh manusia untuk rerun/rollback")
    else:
        transition(inc, S.AWAITING_APPROVAL if dec.verdict == Verdict.NEED_APPROVAL else S.ESCALATED)
    db.decisions.put(dec); db.incidents.put(inc)
    if notify: notify(inc, dec, f"🟥 {inc.summary}")
    return {"ok": False, "results": results, "missing": missing}


def process_pending(notify=None) -> dict[str, Any]:
    n = 0; ok = 0
    for inc in db.incidents.list(rule="fin_ok_pending_verify", limit=10):
        if inc.state not in (S.TRIAGED, S.DECIDED, S.DETECTED):
            continue
        # tick membuat keputusan 'verify' sebagai NOTIFY; di sini verifikasi sesungguhnya
        r = verify_incident(inc, notify); n += 1; ok += int(r["ok"])
    return {"verified": n, "ok": ok}
