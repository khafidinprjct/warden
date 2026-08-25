"""Penerimaan denyut & marker dari harness (HMAC). Tidak berpikir — hanya memvalidasi dan menyimpan."""
from __future__ import annotations

import hashlib
import hmac
import json

from warden.config import settings
from warden.core.models import Heartbeat, Marker
from warden.store import firestore as db


def sign(body: bytes, secret: str | None = None) -> str:
    return hmac.new((secret or settings.ingest_hmac_secret).encode(), body, hashlib.sha256).hexdigest()


def verify(body: bytes, sig: str) -> bool:
    return hmac.compare_digest(sign(body), sig or "")


def validate_marker(mk: Marker) -> Marker:
    """Marker sah = RUN_FIN dengan exit_code, run_id, tanda tangan benar (mode #5/#6/#11)."""
    if mk.kind == "RUN_FIN":
        if mk.exit_code is None:
            mk.valid, mk.invalid_reason = False, "tanpa exit_code"
        elif not mk.run_id:
            mk.valid, mk.invalid_reason = False, "tanpa run_id"
        else:
            expect = sign(f"{mk.job_id}|{mk.run_id}|{mk.exit_code}|{mk.ts.isoformat()}".encode())
            if not hmac.compare_digest(expect, mk.signature or ""):
                mk.valid, mk.invalid_reason = False, "tanda tangan tidak cocok"
            else:
                mk.valid = True
    elif mk.kind == "RUN_START":
        mk.valid = bool(mk.run_id); mk.invalid_reason = "" if mk.run_id else "tanpa run_id"
    else:
        mk.valid = True
    return mk


def _touch_job(job_id: str, **fields) -> None:
    """Perbarui ringkasan job dari denyut/marker (run_id, fase, step, waktu denyut) — sumber kebenaran tetap Firestore."""
    job = db.jobs.get(job_id)
    if job is None:
        return
    changed = False
    for k, v in fields.items():
        if v in (None, "") or getattr(job, k) == v:
            continue
        if k == "run_id" and job.run_id and job.run_id != v and job.status.value in ("COMPLETE",):
            continue
        setattr(job, k, v); changed = True
    if changed:
        db.jobs.put(job)


def ingest_heartbeat(payload: dict) -> Heartbeat:
    hb = Heartbeat.model_validate(payload)
    db.put_heartbeat(hb)
    _touch_job(hb.job_id, run_id=hb.run_id, phase=hb.phase, last_step=hb.step, last_heartbeat_at=hb.ts)
    return hb


def ingest_marker(payload: dict) -> Marker:
    mk = validate_marker(Marker.model_validate(payload))
    db.put_marker(mk)
    if mk.kind in ("RUN_START", "RUN_FIN") and mk.valid:
        _touch_job(mk.job_id, run_id=mk.run_id, phase=mk.phase or None)
    return mk
