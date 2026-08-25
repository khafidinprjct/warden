"""Registry tindakan (R1 dry_run di semua aksi). Tidak ada delete. Setiap aksi: audit niat → jalankan → audit hasil (P10)."""
from __future__ import annotations

from typing import Any, Callable

from warden.core.models import Action, AuditEntry, Decision, now
from warden.providers.base import OpResult
from warden.store import firestore as db


def _notify(dec: Decision, compute, dry_run: bool) -> OpResult:
    return OpResult(True, "notify", observed="dikirim" if not dry_run else "", dry_run=dry_run, plan={"channel": "discord"})


def _start(dec: Decision, compute, dry_run: bool) -> OpResult:
    return compute.start(dec.params["instance_ref"], dry_run=dry_run)


def _stop(dec: Decision, compute, dry_run: bool) -> OpResult:
    return compute.stop(dec.params["instance_ref"], dry_run=dry_run)


def _resume(dec: Decision, compute, dry_run: bool) -> OpResult:
    """Resume = kirim perintah ke mailbox harness (metadata warden-cmd) — harness yang menjalankan wrun ulang dari VERIFIED."""
    items = {"warden-cmd": f"resume:{dec.params.get('run_id','')}:{dec.params.get('ckpt','')}"}
    return compute.set_metadata(dec.params["instance_ref"], items, dry_run=dry_run)


def _kill(dec: Decision, compute, dry_run: bool) -> OpResult:
    return compute.set_metadata(dec.params["instance_ref"], {"warden-cmd": f"kill:{dec.params.get('pid','')}"}, dry_run=dry_run)


def _quarantine(dec: Decision, compute, dry_run: bool) -> OpResult:
    return compute.set_metadata(dec.params["instance_ref"], {"warden-cmd": f"quarantine:{dec.params.get('path','')}"}, dry_run=dry_run)


def _rollback(dec: Decision, compute, dry_run: bool) -> OpResult:
    return compute.set_metadata(dec.params["instance_ref"], {"warden-cmd": f"rollback:{dec.params.get('ckpt','')}"}, dry_run=dry_run)


def _unsupported(dec: Decision, compute, dry_run: bool) -> OpResult:
    return OpResult(False, dec.action.value, error="belum diimplementasikan (Fase 12)")


HANDLERS: dict[Action, Callable[[Decision, Any, bool], OpResult]] = {
    Action.NOTIFY: _notify, Action.START_INSTANCE: _start, Action.STOP_INSTANCE: _stop,
    Action.RESUME_JOB: _resume, Action.KILL_PROCESS: _kill, Action.QUARANTINE_ARTIFACT: _quarantine,
    Action.ROLLBACK_LAST_GOOD: _rollback, Action.RELOCATE_ZONE: _unsupported,
    Action.RESIZE_DISK: _unsupported, Action.CHANGE_MACHINE_TYPE: _unsupported,
}
assert not any(a.value.startswith("delete") for a in HANDLERS), "delete tidak boleh ada (P8)"


def dry_run(dec: Decision, compute) -> dict[str, Any]:
    r = HANDLERS[dec.action](dec, compute, True)
    return {"ok": r.ok, "requested": r.requested, "plan": r.plan, "error": r.error}


def execute(dec: Decision, compute, actor: str = "warden") -> OpResult:
    """Eksekusi dengan lease per job + audit niat/hasil. Pemanggil sudah memastikan verdict AUTO/APPROVED."""
    holder = f"exec:{dec.decision_id}"
    if dec.job_id and not db.acquire_lease(dec.job_id, holder, ttl_s=300):
        return OpResult(False, dec.action.value, error="lease job dipegang pihak lain (anti balapan)")
    target = dec.params.get("instance_ref", dec.job_id)
    db.audit(AuditEntry(actor=actor, phase="intent", action=dec.action.value, target=target, decision_id=dec.decision_id,
                        before={"params": dec.params, "explain": dec.explain, "blast_radius": dec.blast_radius}))
    try:
        r = HANDLERS[dec.action](dec, compute, False)
    except Exception as e:  # tetap tercatat, tidak ditelan
        r = OpResult(False, dec.action.value, error=f"{type(e).__name__}: {e}")
    finally:
        if dec.job_id:
            db.release_lease(dec.job_id, holder)
    db.audit(AuditEntry(actor=actor, phase="result", action=dec.action.value, target=target, decision_id=dec.decision_id,
                        after={"observed": r.observed, "op_id": r.op_id, "plan": r.plan}, ok=r.ok, error=r.error))
    dec.result = {"requested": r.requested, "observed": r.observed, "ok": r.ok, "error": r.error, "op_id": r.op_id}
    return r
