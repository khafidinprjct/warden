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
        return {"ok": False, "error": "decision not found"}
    if dec.status != DecisionStatus.PENDING or dec.verdict != Verdict.NEED_APPROVAL:
        return {"ok": False, "error": f"status {dec.status} / {dec.verdict} — not awaiting approval"}
    if dec.expires_at and dec.expires_at < now():
        dec.status = DecisionStatus.EXPIRED; db.decisions.put(dec)
        return {"ok": False, "error": "expired"}
    inc = db.incidents.get(dec.incident_id)
    dec.status, dec.approved_by = DecisionStatus.EXECUTING, who; db.decisions.put(dec)
    if inc and inc.state == S.AWAITING_APPROVAL:
        transition(inc, S.EXECUTING, note=f"approved by {who}", actor=f"human:{who}")
    r = ex.execute(dec, compute(), actor=f"human:{who}")
    dec.status = DecisionStatus.DONE if r.ok else DecisionStatus.FAILED; db.decisions.put(dec)
    if inc:
        from warden.executor import recovery
        recovery.after_execute(inc, dec, r)     # VERIFYING with a world-check (or next hypothesis if the call itself failed)
        db.incidents.put(inc)
    return {"ok": r.ok, "observed": r.observed, "error": r.error, "decision_id": decision_id}


def deny(decision_id: str, who: str, reason: str = "") -> dict:
    dec = db.decisions.get(decision_id)
    if dec is None or dec.status != DecisionStatus.PENDING:
        return {"ok": False, "error": "not awaiting approval"}
    dec.status, dec.approved_by = DecisionStatus.REJECTED, who; db.decisions.put(dec)
    inc = db.incidents.get(dec.incident_id)
    if inc and inc.state == S.AWAITING_APPROVAL:
        transition(inc, S.CLOSED, note=f"denied by {who}: {reason}", actor=f"human:{who}"); db.incidents.put(inc)
    return {"ok": True, "decision_id": decision_id}


def freeze(who: str, on: bool) -> dict:
    """Tombol merah global (R2): semua tindakan → HELD sampai dilepas."""
    db.client().collection("policies").document("runtime").set({"frozen": on, "frozen_by": who, "frozen_at": now().isoformat()}, merge=True)
    return {"ok": True, "frozen": on}


def expire_stale(notify=None) -> int:
    """A request that times out must say so.

    Letting it lapse in silence is the worst of both worlds: Warden did not act, and the human never learned they were
    needed — while whatever the action would have stopped keeps running and keeps costing. The operator is told what
    expired, what it would have done, and what the wait is costing per day, with the option to re-evaluate it now.
    """
    n = 0
    for dec in db.decisions.list(status="PENDING", limit=200):
        if dec.expires_at and dec.expires_at < now():
            dec.status = DecisionStatus.EXPIRED; db.decisions.put(dec); n += 1
            inc = db.incidents.get(dec.incident_id)
            if inc and inc.state == S.AWAITING_APPROVAL:
                transition(inc, S.ESCALATED, note="approval expired"); db.incidents.put(inc)
            if notify:
                daily = (inc.cost_burning_usd_per_hour * 24) if inc else 0.0
                cost = f" It is costing ${daily:,.2f} a day while it waits." if daily > 0 else ""
                notify(inc, dec, f"⏰ Nobody answered in time — **{dec.action.value.replace('_', ' ')}** on "
                                 f"{dec.job_id or 'the fleet'} was not run.{cost} "
                                 f"Re-evaluate it to decide again with the policy as it stands now.")
    return n


def always(decision_id: str, who: str, hours: int = 24) -> dict:
    """Setujui + naikkan tindakan ini ke L2 selama N jam untuk job yang sama (override kedaluwarsa otomatis)."""
    dec = db.decisions.get(decision_id)
    if dec is None:
        return {"ok": False, "error": "decision not found"}
    r = approve(decision_id, who)
    if r.get("ok"):
        db.client().collection("policy_overrides").document(f"{dec.job_id}:{dec.action.value}").set(
            {"level": "L2", "until": now().timestamp() + hours * 3600, "by": who})
        r["override"] = f"{dec.job_id}:{dec.action.value} → L2 {hours}h"
    return r


def reevaluate(decision_id: str, who: str) -> dict:
    """Keputusan yang kedaluwarsa/ditolak dinilai ulang dengan konteks SEKARANG (breaker, limit, frozen) →
    keputusan baru: AUTO dieksekusi, NEED_APPROVAL menunggu lagi. Tidak pernah melewati kebijakan."""
    from warden.policy.engine import evaluate as policy_eval, load_policy
    from warden.watcher.tick import _ctx_for, _is_frozen
    dec = db.decisions.get(decision_id)
    if dec is None:
        return {"ok": False, "error": "decision not found"}
    if dec.status not in (DecisionStatus.EXPIRED, DecisionStatus.REJECTED, DecisionStatus.FAILED) and not (
            dec.status == DecisionStatus.PENDING and dec.expires_at and dec.expires_at < now()):
        return {"ok": False, "error": f"status {dec.status} — only EXPIRED/REJECTED/FAILED can be re-evaluated"}
    inc = db.incidents.get(dec.incident_id)
    job = db.jobs.get(dec.job_id) if dec.job_id else None
    inst = None
    ref = dec.params.get("instance_ref") or (inc.instance_ref if inc else "")
    if ref:
        try:
            inst = compute().describe(ref)
        except Exception:  # noqa: BLE001 — mesin tak ditemukan: tetap nilai tanpa konteks mesin
            inst = None
    if dec.status == DecisionStatus.PENDING:
        dec.status = DecisionStatus.EXPIRED; db.decisions.put(dec)
    new = policy_eval(dec.action, _ctx_for(job, inst, dec.action, _is_frozen()), load_policy())
    new.incident_id, new.job_id, new.params = dec.incident_id, dec.job_id, dict(dec.params)
    new.explain = [f"re-evaluated by {who} from {dec.decision_id}"] + list(new.explain)
    new.dry_run_plan = ex.dry_run(new, compute())
    db.decisions.put(new)
    if inc:
        inc.decision_ids.append(new.decision_id)
        inc.timeline.append({"ts": now().isoformat(), "from": str(inc.state), "to": str(inc.state),
                             "note": f"re-evaluated → {new.action}: {new.verdict} ({new.autonomy})", "actor": f"human:{who}"})
    if new.verdict == Verdict.AUTO:
        new.status = DecisionStatus.EXECUTING; db.decisions.put(new)
        if inc:
            inc.state = S.EXECUTING
        r = ex.execute(new, compute(), actor=f"human:{who}")
        new.status = DecisionStatus.DONE if r.ok else DecisionStatus.FAILED; db.decisions.put(new)
        if inc:
            from warden.executor import recovery
            recovery.after_execute(inc, new, r)
            db.incidents.put(inc)
        return {"ok": r.ok, "observed": r.observed, "error": r.error, "decision_id": new.decision_id, "verdict": str(new.verdict)}
    if inc:
        inc.state = S.AWAITING_APPROVAL if new.verdict == Verdict.NEED_APPROVAL else S.HELD if new.verdict == Verdict.HELD else S.ESCALATED
        inc.updated_at = now(); db.incidents.put(inc)
    return {"ok": True, "decision_id": new.decision_id, "verdict": str(new.verdict), "autonomy": str(new.autonomy)}


def false_positive(incident_id: str, who: str, reason: str = "") -> dict:
    """The human says this alarm was wrong. Pending decisions are rejected; memory lowers the weight of this rule for this job (F3)."""
    inc = db.incidents.get(incident_id)
    if inc is None:
        return {"ok": False, "error": "incident not found"}
    if inc.state not in (S.AWAITING_APPROVAL, S.ESCALATED):
        return {"ok": False, "error": f"state {inc.state} — only AWAITING_APPROVAL/ESCALATED can be marked false positive"}
    for did in inc.decision_ids:
        d = db.decisions.get(did)
        if d and d.status == DecisionStatus.PENDING:
            d.status, d.approved_by = DecisionStatus.REJECTED, who; db.decisions.put(d)
    transition(inc, S.FALSE_POSITIVE, note=f"false positive by {who}: {reason}"[:200], actor=f"human:{who}"); db.incidents.put(inc)
    return {"ok": True, "incident_id": incident_id}


# The operator reads this sentence in the incident list; the action's storage id does not belong in it.
ACTION_TEXT = {"notify": "Notify", "start_instance": "Start instance", "resume_job": "Resume job", "stop_instance": "Stop instance",
               "quarantine_artifact": "Quarantine artifact", "rollback_last_good": "Roll back to last good checkpoint",
               "relocate_zone": "Relocate zone", "resize_disk": "Resize disk", "kill_process": "Kill process",
               "change_machine_type": "Change machine type", "clean_disk": "Clean disk"}


def propose(job_id: str, action: str, params: dict | None, who: str, why: str = "") -> dict:
    """A human (dashboard / Ask Warden) asks for an action. It goes through the SAME policy and approval path as Warden's own
    proposals: policy-evaluated, dry-run planned, then executed (AUTO) or queued for approval — never a side door."""
    from warden.core.models import Action, Incident, IncidentState as S
    from warden.executor import recovery
    from warden.policy.engine import evaluate as policy_eval
    from warden.watcher.tick import _ctx_for, _is_frozen, _policy_for
    try:
        act = Action(action)
    except ValueError:
        return {"ok": False, "error": f"unknown action {action}"}
    job = db.jobs.get(job_id)
    if job is None:
        return {"ok": False, "error": "job not found"}
    inst = compute().describe(job.instance_ref) if job.instance_ref else None
    inc = Incident(job_id=job_id, instance_ref=job.instance_ref, rule="operator_request", severity="info", summary=(f"Operator requested {ACTION_TEXT.get(action, action.replace('_', ' ').capitalize())} on {job_id}"
                            + (f" — {why}" if why else ""))[:300],
                   dedupe_key=f"operator:{job_id}:{action}:{now().strftime('%Y%m%d%H%M%S')}")
    transition(inc, S.TRIAGED, note=f"Requested {ACTION_TEXT.get(action, action).lower()} from the {who}",
               actor=f"human:{who}")
    dec = policy_eval(act, _ctx_for(job, inst, act, _is_frozen()), _policy_for(job))
    dec.incident_id = inc.incident_id; dec.job_id = job_id
    dec.params = {"instance_ref": job.instance_ref, "run_id": job.run_id, **(params or {}), "reason": why or f"requested by {who}"}
    dec.explain.insert(0, f"operator request by {who}")
    if act != Action.NOTIFY:
        dec.dry_run_plan = ex.dry_run(dec, compute())
    db.decisions.put(dec); inc.decision_ids.append(dec.decision_id)
    transition(inc, S.DECIDED, note=f"{act}: {dec.verdict}")
    if dec.verdict == Verdict.AUTO:
        transition(inc, S.EXECUTING); dec.status = DecisionStatus.EXECUTING; db.decisions.put(dec)
        r = ex.execute(dec, compute(), actor=f"human:{who}")
        db.incidents.put(inc); recovery.after_execute(inc, dec, r)
        db.incidents.put(inc)
        return {"ok": r.ok, "incident_id": inc.incident_id, "decision_id": dec.decision_id, "verdict": str(dec.verdict), "observed": r.observed, "error": r.error}
    transition(inc, S.AWAITING_APPROVAL if dec.verdict == Verdict.NEED_APPROVAL else S.HELD if dec.verdict == Verdict.HELD else S.ESCALATED)
    db.incidents.put(inc)
    return {"ok": True, "incident_id": inc.incident_id, "decision_id": dec.decision_id, "verdict": str(dec.verdict), "explain": dec.explain}
