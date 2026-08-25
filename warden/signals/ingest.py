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
    """Tanda tangan sah bila cocok dengan secret aktif ATAU secret sebelumnya (masa tenggang rotasi, lihat infra/rotate_hmac.py)."""
    if hmac.compare_digest(sign(body), sig or ""):
        return True
    prev = settings.ingest_hmac_secret_prev
    return bool(prev) and hmac.compare_digest(sign(body, prev), sig or "")


def validate_marker(mk: Marker) -> Marker:
    """Marker sah = RUN_FIN dengan exit_code, run_id, tanda tangan benar (mode #5/#6/#11)."""
    if mk.kind == "RUN_FIN":
        if mk.exit_code is None:
            mk.valid, mk.invalid_reason = False, "missing exit_code"
        elif not mk.run_id:
            mk.valid, mk.invalid_reason = False, "missing run_id"
        else:
            expect = sign(f"{mk.job_id}|{mk.run_id}|{mk.exit_code}|{mk.ts.isoformat()}".encode())
            if not hmac.compare_digest(expect, mk.signature or ""):
                mk.valid, mk.invalid_reason = False, "signature mismatch"
            else:
                mk.valid = True
    elif mk.kind == "RUN_START":
        mk.valid = bool(mk.run_id); mk.invalid_reason = "" if mk.run_id else "missing run_id"
    else:
        mk.valid = True
    return mk


def _run_start_ts(job_id: str, run_id: str):
    mk = db.get_marker(job_id, run_id, "RUN_START") if run_id else None
    return mk.ts if mk else None


def _touch_job(job_id: str, source: str = "marker", **fields) -> None:
    """Perbarui ringkasan job dari denyut/marker (run_id, fase, step, waktu denyut) — sumber kebenaran tetap Firestore.
    Denyut BASI tidak boleh memundurkan run_id (insiden 25 Agu: train.json run lama membuat RUN_FIN exit 1 run baru tak terlihat)."""
    job = db.jobs.get(job_id)
    if job is None:
        return
    changed = False
    for k, v in fields.items():
        if v in (None, "") or getattr(job, k) == v:
            continue
        if k == "run_id" and job.run_id and job.run_id != v and job.status.value in ("COMPLETE",):
            continue
        if k == "run_id" and source == "heartbeat" and job.run_id and job.run_id != v:
            cur, new = _run_start_ts(job_id, job.run_id), _run_start_ts(job_id, v)
            if cur is not None and (new is None or new < cur):
                return   # seluruh denyut ini milik run lama → abaikan (jangan timpa fase/step run baru)
        setattr(job, k, v); changed = True
    if changed:
        db.jobs.put(job)


def ingest_heartbeat(payload: dict) -> Heartbeat:
    hb = Heartbeat.model_validate(payload)
    db.put_heartbeat(hb)
    _touch_job(hb.job_id, source="heartbeat", run_id=hb.run_id, phase=hb.phase, last_step=hb.step, last_heartbeat_at=hb.ts)
    return hb


def ingest_marker(payload: dict) -> Marker:
    mk = validate_marker(Marker.model_validate(payload))
    db.put_marker(mk)
    if mk.kind in ("RUN_START", "RUN_FIN") and mk.valid:
        _touch_job(mk.job_id, run_id=mk.run_id, phase=mk.phase or None)
    return mk
