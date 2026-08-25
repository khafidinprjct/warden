"""Persetujuan manusia (hidup di Firestore, bukan di sesi LLM). approve() mengeksekusi keputusan yang menunggu."""
from __future__ import annotations

from warden.core.models import DecisionStatus, IncidentState as S, Verdict, now
from warden.core.state_machine import transition
from warden.executor import registry as ex
from warden.providers.registry import compute
from warden.store import firestore as db


def approve(decision_id: str, who: str) -> dict:
    dec = db.decisions.get(decision_id)
    if dec is None:
        return {"ok": False, "error": "keputusan tidak ada"}
    if dec.status != DecisionStatus.PENDING or dec.verdict != Verdict.NEED_APPROVAL:
        return {"ok": False, "error": f"status {dec.status} / {dec.verdict} — tidak menunggu izin"}
    if dec.expires_at and dec.expires_at < now():
        dec.status = DecisionStatus.EXPIRED; db.decisions.put(dec)
        return {"ok": False, "error": "kedaluwarsa"}
    inc = db.incidents.get(dec.incident_id)
    dec.status, dec.approved_by = DecisionStatus.EXECUTING, who; db.decisions.put(dec)
    if inc and inc.state == S.AWAITING_APPROVAL:
        transition(inc, S.EXECUTING, note=f"disetujui {who}", actor=f"human:{who}")
    r = ex.execute(dec, compute(), actor=f"human:{who}")
    dec.status = DecisionStatus.DONE if r.ok else DecisionStatus.FAILED; db.decisions.put(dec)
    if inc:
        transition(inc, S.VERIFYING if r.ok else S.FAILED_ACTION, note=r.observed or r.error)
        transition(inc, S.RESOLVED if r.ok else S.ESCALATED, note="diminta-vs-jadi" if r.ok else r.error)
        db.incidents.put(inc)
    return {"ok": r.ok, "observed": r.observed, "error": r.error, "decision_id": decision_id}


def deny(decision_id: str, who: str, reason: str = "") -> dict:
    dec = db.decisions.get(decision_id)
    if dec is None or dec.status != DecisionStatus.PENDING:
        return {"ok": False, "error": "tidak menunggu izin"}
    dec.status, dec.approved_by = DecisionStatus.REJECTED, who; db.decisions.put(dec)
    inc = db.incidents.get(dec.incident_id)
    if inc and inc.state == S.AWAITING_APPROVAL:
        transition(inc, S.CLOSED, note=f"ditolak {who}: {reason}", actor=f"human:{who}"); db.incidents.put(inc)
    return {"ok": True, "decision_id": decision_id}


def freeze(who: str, on: bool) -> dict:
    """Tombol merah global (R2): semua tindakan → HELD sampai dilepas."""
    db.client().collection("policies").document("runtime").set({"frozen": on, "frozen_by": who, "frozen_at": now().isoformat()}, merge=True)
    return {"ok": True, "frozen": on}


def expire_stale() -> int:
    n = 0
    for dec in db.decisions.list(status="PENDING", limit=200):
        if dec.expires_at and dec.expires_at < now():
            dec.status = DecisionStatus.EXPIRED; db.decisions.put(dec); n += 1
            inc = db.incidents.get(dec.incident_id)
            if inc and inc.state == S.AWAITING_APPROVAL:
                transition(inc, S.ESCALATED, note="izin kedaluwarsa"); db.incidents.put(inc)
    return n
