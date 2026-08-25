"""Action registry: every recommendation the Diagnostician can make has a real executor here.
Every action supports dry_run (R1). No delete of machines or disks exists (P8). Audit intent → run → audit result (P10).

Two kinds of executors:
  • provider actions   — Compute Engine API (start / stop / relocate / set_machine_type / resize_disk)
  • mailbox actions    — signed command for the harness on the machine (resume / kill / quarantine / rollback / clean_disk / grow_fs);
                         the harness reports back through /ingest/cmd_result and the verifier closes the loop.
"""
from __future__ import annotations

from typing import Any, Callable

from warden.core.models import Action, AuditEntry, Decision, now
from warden.providers.base import OpResult
from warden.signals import ingest as ing
from warden.store import firestore as db


# ----------------------------------------------------------------------------- mailbox
def _mailbox(dec: Decision, cmd: str, args: dict[str, Any], dry_run: bool) -> OpResult:
    plan = {"channel": "mailbox", "cmd": cmd, "args": args, "job_id": dec.job_id}
    if dry_run:
        return OpResult(True, f"{cmd} {dec.job_id}", dry_run=True, plan=plan)
    if not dec.job_id:
        return OpResult(False, f"{cmd} ?", error="no job_id — mailbox commands need a job", plan=plan)
    doc = db.mailbox_post(dec.job_id, cmd, args, dec.decision_id, signer=ing.sign_cmd)
    return OpResult(True, f"{cmd} {dec.job_id}", observed=f"queued nonce={doc['nonce']}", op_id=doc["nonce"], plan=plan)


def _notify(dec: Decision, compute, dry_run: bool) -> OpResult:
    return OpResult(True, "notify", observed="sent" if not dry_run else "", dry_run=dry_run, plan={"channel": "dashboard+discord"})


def _start(dec: Decision, compute, dry_run: bool) -> OpResult:
    r = compute.start(dec.params["instance_ref"], dry_run=dry_run)
    if not r.ok and not dry_run and "ZONE_RESOURCE_POOL_EXHAUSTED" in (r.error or ""):
        inst = compute.describe(dec.params["instance_ref"])
        db.stockout_mark(dec.params["instance_ref"].split("/")[0], inst.machine_type if inst else "", r.error)
    return r


def _stop(dec: Decision, compute, dry_run: bool) -> OpResult:
    return compute.stop(dec.params["instance_ref"], dry_run=dry_run)


def _resume_env(p: dict[str, Any]) -> dict[str, str]:
    """Environment the harness exports to the resumed command. The job reads WARDEN_* scales; PyTorch reads the allocator hint."""
    env: dict[str, str] = {}
    if p.get("batch_scale"):
        env["WARDEN_BATCH_SCALE"] = str(p["batch_scale"]); env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    if p.get("workers_scale"):
        env["WARDEN_WORKERS_SCALE"] = str(p["workers_scale"])
    if p.get("lr_scale"):
        env["WARDEN_LR_SCALE"] = str(p["lr_scale"])
    if p.get("ckpt"):
        env["WARDEN_RESUME_CKPT"] = str(p["ckpt"])
    return env


def _resume(dec: Decision, compute, dry_run: bool) -> OpResult:
    """resume_job: mode same | smaller_batch | fewer_workers | clean. The harness re-runs the job's resume command
    (wrun) with the given environment; 'clean' archives the artifacts directory first (moved aside, not deleted)."""
    p = dec.params
    args = {"mode": p.get("mode", "same"), "env": _resume_env(p), "clean": p.get("mode") == "clean", "reason": p.get("reason", "")}
    return _mailbox(dec, "resume", args, dry_run)


def _kill(dec: Decision, compute, dry_run: bool) -> OpResult:
    return _mailbox(dec, "kill", {"pid": dec.params.get("pid"), "then_resume": bool(dec.params.get("then_resume", False)), "env": _resume_env(dec.params)}, dry_run)


def _quarantine(dec: Decision, compute, dry_run: bool) -> OpResult:
    return _mailbox(dec, "quarantine", {"path": dec.params.get("path", "")}, dry_run)


def _rollback(dec: Decision, compute, dry_run: bool) -> OpResult:
    """rollback_last_good: checkpoints newer than the target are set aside (.rolledback, not deleted), then resume with lr scaled."""
    p = dec.params
    job = db.jobs.get(dec.job_id) if dec.job_id else None
    ckpt = p.get("ckpt") or ((job.last_good_ckpt or {}).get("path", "") if job else "")
    args = {"ckpt": ckpt, "back": int(p.get("back", 1)), "env": _resume_env({**p, "ckpt": ckpt}), "reason": p.get("reason", "")}
    return _mailbox(dec, "rollback", args, dry_run)


def _clean_disk(dec: Decision, compute, dry_run: bool) -> OpResult:
    """Delete LOCAL checkpoint files whose hash matches the copy in Storage, keeping the newest N. Files only — never disks."""
    return _mailbox(dec, "clean_disk", {"keep": int(dec.params.get("keep", 2)), "min_free_gb": float(dec.params.get("min_free_gb", 5.0))}, dry_run)


def _pick_zone(dec: Decision, compute) -> str:
    """Target zone for relocation: explicit param → job.zone_candidates → other zones of the same region, skipping recent stock-outs."""
    ref = dec.params.get("instance_ref", ""); zone = ref.split("/")[0] if ref else ""
    inst = compute.describe(ref) if ref else None
    mt = inst.machine_type if inst else ""
    job = db.jobs.get(dec.job_id) if dec.job_id else None
    cands = [dec.params["target_zone"]] if dec.params.get("target_zone") else list(job.zone_candidates if job else [])
    if not cands and zone:
        region = zone.rsplit("-", 1)[0]
        cands = [f"{region}-{s}" for s in ("a", "b", "c", "f")]
    for z in cands:
        if z != zone and not db.stockout_recent(z, mt):
            return z
    return ""


def _relocate(dec: Decision, compute, dry_run: bool) -> OpResult:
    ref = dec.params["instance_ref"]
    target = _pick_zone(dec, compute)
    if not target:
        return OpResult(False, f"relocate {ref}", error="no candidate zone without a recent stock-out")
    inst = compute.describe(ref)
    if inst is not None and str(inst.status) == "RUNNING" and not dry_run:
        rs = compute.stop(ref)            # relocation moves the disk: the source must be stopped first (the job resumes from its checkpoint on the new machine)
        if not rs.ok:
            return OpResult(False, f"relocate {ref}", error=f"stop before relocation failed: {rs.error}")
    r = compute.relocate(ref, target, dry_run=dry_run)
    if r.ok and not dry_run:
        job = db.jobs.get(dec.job_id) if dec.job_id else None
        if job:
            job.instance_ref = r.observed; db.jobs.put(job)
        dec.params["new_instance_ref"] = r.observed
    elif not r.ok and not dry_run and "ZONE_RESOURCE_POOL_EXHAUSTED" in (r.error or ""):
        inst = compute.describe(ref)
        db.stockout_mark(target, inst.machine_type if inst else "", r.error)
    r.plan = {**(r.plan or {}), "target_zone": target}
    return r


def _change_machine_type(dec: Decision, compute, dry_run: bool) -> OpResult:
    """Stop (if running) → set machine type → start. 'mode: bigger' picks the next size of the same family."""
    ref = dec.params["instance_ref"]
    inst = compute.describe(ref)
    if inst is None:
        return OpResult(False, f"change_machine_type {ref}", error="instance not found")
    mt = dec.params.get("machine_type", "")
    if not mt and dec.params.get("mode", "bigger") == "bigger":
        from warden.providers.gce import bigger_machine
        mt = bigger_machine(inst.machine_type)
    if not mt:
        return OpResult(False, f"change_machine_type {ref}", error=f"no bigger machine known for {inst.machine_type}")
    dec.params["machine_type"] = mt
    if dry_run:
        r = compute.set_machine_type(ref, mt, dry_run=True)
        r.plan = {**(r.plan or {}), "sequence": ["stop" if str(inst.status) == "RUNNING" else "(already stopped)", "setMachineType", "start"]}
        return r
    if str(inst.status) == "RUNNING":
        rs = compute.stop(ref)
        if not rs.ok:
            return OpResult(False, f"change_machine_type {ref}", error=f"stop failed: {rs.error}")
    r = compute.set_machine_type(ref, mt)
    if not r.ok:
        return r
    rs = compute.start(ref)
    if not rs.ok:
        return OpResult(False, f"change_machine_type {ref}", error=f"type set to {mt} but start failed: {rs.error}", plan=r.plan)
    return OpResult(True, f"change_machine_type {ref}", observed=f"{mt} RUNNING", op_id=rs.op_id, plan=r.plan)


def _resize_disk(dec: Decision, compute, dry_run: bool) -> OpResult:
    """Grow the boot disk by grow_pct (default 50%) or to size_gb, then ask the harness to grow the filesystem."""
    ref = dec.params["instance_ref"]
    size = int(dec.params.get("size_gb", 0))
    if not size:
        cur = int((compute.resize_disk(ref, 10 ** 6, dry_run=True).plan or {}).get("from_gb", 20))
        size = int(cur * (1 + float(dec.params.get("grow_pct", 50)) / 100.0))
    dec.params["size_gb"] = size
    r = compute.resize_disk(ref, size, dry_run=dry_run)
    if r.ok and not dry_run:
        _mailbox(dec, "grow_fs", {}, False)
    return r


HANDLERS: dict[Action, Callable[[Decision, Any, bool], OpResult]] = {
    Action.NOTIFY: _notify, Action.START_INSTANCE: _start, Action.STOP_INSTANCE: _stop,
    Action.RESUME_JOB: _resume, Action.KILL_PROCESS: _kill, Action.QUARANTINE_ARTIFACT: _quarantine,
    Action.ROLLBACK_LAST_GOOD: _rollback, Action.RELOCATE_ZONE: _relocate,
    Action.RESIZE_DISK: _resize_disk, Action.CHANGE_MACHINE_TYPE: _change_machine_type, Action.CLEAN_DISK: _clean_disk,
}
assert not any(a.value.startswith("delete") for a in HANDLERS), "delete tidak boleh ada (P8)"
assert set(HANDLERS) == set(Action), "every Action must have an executor"


def dry_run(dec: Decision, compute) -> dict[str, Any]:
    try:
        r = HANDLERS[dec.action](dec, compute, True)
    except Exception as e:  # noqa: BLE001 — a dry run must never raise into the caller
        return {"ok": False, "requested": dec.action.value, "plan": {}, "error": f"{type(e).__name__}: {e}"}
    return {"ok": r.ok, "requested": r.requested, "plan": r.plan, "error": r.error}


def execute(dec: Decision, compute, actor: str = "warden") -> OpResult:
    """Eksekusi dengan lease per job + audit niat/hasil. Pemanggil sudah memastikan verdict AUTO/APPROVED."""
    holder = f"exec:{dec.decision_id}"
    if dec.job_id and not db.acquire_lease(dec.job_id, holder, ttl_s=300):
        return OpResult(False, dec.action.value, error="job lease held by another party (race guard)")
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
