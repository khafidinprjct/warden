"""Closing the loop (checklist E): every executed action is VERIFIED against the world, and when the world does not change
as predicted the incident moves to the next hypothesis on a per-category ladder — a human is asked only when the ladder is
exhausted or policy demands it. State lives on the incident (verify, attempt, ladder) so a restart of warden-core resumes it.

  execute → VERIFYING (spec with deadline) → tick: check() → RESOLVED
                                                        ↘ fail → FAILED_ACTION → DECIDED (next rung) → EXECUTING / AWAITING_APPROVAL
"""
from __future__ import annotations

import json as _j
from datetime import datetime, timedelta
from typing import Any

from warden.core.models import Action, Decision, DecisionStatus, IncidentState as S, InstanceStatus, Verdict, now
from warden.core.state_machine import transition
from warden.executor import registry as ex
from warden.policy.engine import evaluate as policy_eval, load_policy
from warden.providers.registry import compute
from warden.store import firestore as db

POLICY = load_policy()

# ----------------------------------------------------------------------------- hypothesis ladders (plan §5.3)
# key = diagnosis category (LLM path) or rule (deterministic path). Each rung: action + params + why.
LADDERS: dict[str, list[dict[str, Any]]] = {
    "preempted": [
        {"action": "start_instance", "params": {}, "why": "spot VM was preempted; start it, the harness resumes from the last intact checkpoint"},
        {"action": "relocate_zone", "params": {}, "why": "start failed on stock-out; move the disk to another zone"},
        {"action": "relocate_zone", "params": {"spot": False}, "why": "no zone holds a Spot machine: leave Spot for on-demand (policy decides whether the price increase is allowed)"},
    ],
    "stopped_external": [
        {"action": "start_instance", "params": {}, "why": "VM stopped outside Warden without a RUN_FIN"},
    ],
    "preempt_storm": [
        {"action": "relocate_zone", "params": {}, "why": "preempted 3× in an hour: this zone is not worth another start — move the disk to another zone"},
        {"action": "relocate_zone", "params": {"spot": False}, "why": "storm follows the job: leave Spot for on-demand (policy decides whether the price increase is allowed)"},
    ],
    "preempt": [
        {"action": "start_instance", "params": {}, "why": "preempt confirmed by diagnosis"},
        {"action": "relocate_zone", "params": {}, "why": "start failed on stock-out"},
    ],
    "oom_gpu": [
        {"action": "resume_job", "params": {"mode": "smaller_batch", "batch_scale": 0.5}, "why": "GPU OOM: halve the batch and enable expandable segments"},
        {"action": "resume_job", "params": {"mode": "smaller_batch", "batch_scale": 0.25}, "why": "still OOM at half batch: quarter batch"},
        {"action": "change_machine_type", "params": {"mode": "bigger"}, "why": "OOM persists at quarter batch: the model needs a bigger machine"},
    ],
    "oom_host": [
        {"action": "resume_job", "params": {"mode": "fewer_workers", "workers_scale": 0.5}, "why": "host OOM: halve the data-loader workers"},
        {"action": "change_machine_type", "params": {"mode": "bigger"}, "why": "host OOM persists: more RAM"},
    ],
    "nan_divergence": [
        {"action": "rollback_last_good", "params": {"lr_scale": 0.5, "back": 1}, "why": "loss diverged: resume from the last intact checkpoint with half the learning rate"},
        {"action": "rollback_last_good", "params": {"lr_scale": 0.25, "back": 2}, "why": "diverged again: two checkpoints back, quarter learning rate"},
        {"action": "stop_instance", "params": {}, "why": "divergence is not a learning-rate problem: stop the spend, human decides"},
    ],
    "disk_full": [
        {"action": "clean_disk", "params": {"keep": 2}, "why": "disk full: delete local checkpoints that already live in Storage (hash-verified)"},
        {"action": "resize_disk", "params": {"grow_pct": 50}, "why": "nothing left to clean: grow the disk"},
    ],
    "disk_low": [
        {"action": "clean_disk", "params": {"keep": 2}, "why": "disk running low: delete local checkpoints that already live in Storage"},
        {"action": "resize_disk", "params": {"grow_pct": 50}, "why": "nothing left to clean: grow the disk"},
    ],
    "stuck": [
        {"action": "kill_process", "params": {"then_resume": True}, "why": "heartbeat stale and machine idle: the process hangs; kill it and resume"},
        {"action": "resume_job", "params": {"mode": "clean"}, "why": "hangs again after resume: restart from a clean run directory"},
    ],
    "dup_process": [
        {"action": "kill_process", "params": {"then_resume": True}, "why": "two entrypoints compete; kill both, resume once under the lock"},
    ],
    "network_transient": [
        {"action": "resume_job", "params": {"mode": "same"}, "why": "transient network error: retry"},
        {"action": "resume_job", "params": {"mode": "same"}, "why": "second retry"},
        {"action": "resume_job", "params": {"mode": "same"}, "why": "third retry"},
    ],
    "kernel_fallback": [
        {"action": "stop_instance", "params": {}, "why": "silent kernel fallback burns GPU hours at CPU speed: stop, report throughput evidence"},
    ],
    "plateau": [],
    "instance_missing": [],
    "disk_trend": [
        {"action": "clean_disk", "params": {"keep": 2}, "why": "disk will be full within hours: clean now, before the checkpoint fails"},
        {"action": "resize_disk", "params": {"grow_pct": 50}, "why": "nothing to clean: grow the disk before it fills"},
    ],
    "throughput_drop": [], "grad_spike": [], "vram_creep": [],
}
PERMANENT_STOP = {"nan_input", "dependency_missing", "env_broken", "data_error", "config_error", "code_bug"}
for _k in PERMANENT_STOP:
    LADDERS[_k] = [{"action": "stop_instance", "params": {}, "why": f"{_k} is permanent: stop the machine to stop the spend; patch suggestion goes to the human"}]

REC2RUNG = {  # Diagnostician recommendation → first rung
    "resume_same": ("resume_job", {"mode": "same"}), "resume_smaller_batch": ("resume_job", {"mode": "smaller_batch", "batch_scale": 0.5}),
    "resume_fewer_workers": ("resume_job", {"mode": "fewer_workers", "workers_scale": 0.5}), "restart_clean": ("resume_job", {"mode": "clean"}),
    "rollback_last_good": ("rollback_last_good", {"lr_scale": 0.5, "back": 1}), "kill_and_resume": ("kill_process", {"then_resume": True}),
    "clean_disk": ("clean_disk", {"keep": 2}), "resize_disk": ("resize_disk", {"grow_pct": 50}), "relocate_zone": ("relocate_zone", {}),
    "change_machine_type": ("change_machine_type", {"mode": "bigger"}), "stop": ("stop_instance", {}), "patch_suggest": ("stop_instance", {}),
    "escalate": ("notify", {}), "noop": ("notify", {}),
}


def build_ladder(key: str, first: tuple[str, dict] | None = None, params_from_llm: dict | None = None) -> list[dict[str, Any]]:
    """Ladder for a category/rule; the LLM's own recommendation (if any) becomes rung 1 and duplicates are dropped."""
    rungs: list[dict[str, Any]] = []
    if first and first[0] != "notify":
        rungs.append({"action": first[0], "params": {**first[1], **(params_from_llm or {})}, "why": "diagnostician recommendation"})
    for r in LADDERS.get(key, []):
        # same action whose parameters are already covered by an earlier rung = the same hypothesis, skip it
        if not any(x["action"] == r["action"] and all(x["params"].get(k) == v for k, v in r["params"].items()) for x in rungs):
            rungs.append(dict(r))
    return rungs


def remembered_rung(job_id: str, key: str) -> tuple[dict | None, str]:
    """Memory changes the plan (checklist F2): the most recent postmortem of the same job+category that RESOLVED tells us
    which rung worked → it goes first. One that ESCALATED after rung k tells us to start at k+1."""
    try:
        from warden.agents import memory
        same_job = [p for p in memory.recall(job_id=job_id, rule="", query="", n=20) if p.get("category") == key or p.get("rule") == key]
        other = [] if same_job else [p for p in memory.recall(job_id="", rule="", query="", n=50) if (p.get("category") == key or p.get("rule") == key) and p.get("job_id") != job_id]
    except Exception:  # noqa: BLE001
        return None, ""
    for pm, scope in [(p, "this job") for p in same_job] + [(p, f"job {p.get('job_id')}") for p in other]:  # newest first, own job first (F5)
        acts = [a for a in pm.get("actions", []) if a.get("action") not in ("notify", "")]
        if pm.get("ok") and acts:
            a = acts[-1]
            return {"action": a["action"], "params": a.get("params", {}), "why": f"remembered from {scope}: {pm['incident_id']} resolved with {a['action']}"}, pm["incident_id"]
    return None, ""


# ----------------------------------------------------------------------------- verify specs
def _deadline(action: str) -> datetime:
    m = int(POLICY.get("recovery", {}).get("verify_deadline_minutes", {}).get(action, 6))
    return now() + timedelta(minutes=m)


def spec_for(dec: Decision, inst, job) -> dict[str, Any]:
    hb = db.last_heartbeat(job.job_id) if job else None
    return {"kind": dec.action.value, "since": now().isoformat(), "deadline": _deadline(dec.action.value).isoformat(),
            "decision_id": dec.decision_id, "nonce": (dec.result or {}).get("op_id", "") if dec.action in MAILBOX else "",
            "instance_ref": dec.params.get("new_instance_ref") or dec.params.get("instance_ref", ""),
            "baseline": {"boot_id": (hb.boot_id if hb and hb.boot_id else (inst.boot_id if inst else "")),   # the boot the harness last reported BEFORE the action
                         "run_id": job.run_id if job else "", "step": job.last_step if job else 0,
                         "disk_avail_gb": hb.disk_avail_gb if hb else None, "machine_type": inst.machine_type if inst else "",
                         "hb_ts": hb.ts.isoformat() if hb else None},
            "params": dict(dec.params), "checks": []}


MAILBOX = {Action.RESUME_JOB, Action.KILL_PROCESS, Action.QUARANTINE_ARTIFACT, Action.ROLLBACK_LAST_GOOD, Action.CLEAN_DISK}


def check(inc, spec: dict[str, Any]) -> tuple[str, str]:
    """Return ('ok'|'fail'|'pending', reason) by looking at the world, never at the action's own return value."""
    kind = spec["kind"]; since = datetime.fromisoformat(spec["since"]); base = spec.get("baseline", {})
    job = db.jobs.get(inc.job_id) if inc.job_id else None
    ref = spec.get("instance_ref") or inc.instance_ref
    inst = compute().describe(ref) if ref else None
    hbs = [h for h in (db.recent_heartbeats(inc.job_id, 30) if inc.job_id else []) if h.ts > since]
    late = now() > datetime.fromisoformat(spec["deadline"])
    res = db.cmd_result_get(inc.job_id, spec.get("nonce", "")) if spec.get("nonce") else None
    if res and not res.get("ok", True):
        return "fail", f"harness reported: {res.get('detail', 'error')[:160]}"

    def steps_advance() -> bool:
        st = [h.step for h in hbs if h.step is not None]
        return len(st) >= 2 and st[-1] > st[0]

    def new_run_failed() -> str:
        for h in hbs[-1:]:
            fin = db.get_marker(inc.job_id, h.run_id, "RUN_FIN") if h.run_id else None
            if fin and fin.valid and fin.exit_code not in (None, 0) and fin.ts > since:
                return f"new run {h.run_id} ended with exit={fin.exit_code}"
        return ""

    if kind == "notify":
        return "ok", "notification is not an action on the world"
    if kind == "stop_instance":
        if inst and inst.status in (InstanceStatus.TERMINATED, InstanceStatus.STOPPED):
            return "ok", f"instance {inst.status}"
        return ("fail", f"instance still {inst.status if inst else 'missing'} after deadline") if late else ("pending", "waiting for TERMINATED")
    if kind in ("start_instance", "relocate_zone", "change_machine_type"):
        if inst is None:
            return ("fail", "instance not found") if late else ("pending", "instance not visible yet")
        if inst.status != InstanceStatus.RUNNING:
            return ("fail", f"instance {inst.status}, not RUNNING") if late else ("pending", f"instance {inst.status}")
        if kind == "change_machine_type" and inst.machine_type != spec["params"].get("machine_type", inst.machine_type):
            return "fail", f"machine type is {inst.machine_type}, expected {spec['params'].get('machine_type')}"
        fresh = [h for h in hbs if h.boot_id and h.boot_id != base.get("boot_id")]
        if fresh and (steps_advance() or any(h.procs for h in fresh) or (job and job.legacy and fresh[-1].log_mtime)):
            return "ok", f"RUNNING, new boot {fresh[-1].boot_id[:8]}, {len(fresh)} heartbeats, step {fresh[-1].step}"
        if (f := new_run_failed()):
            return "fail", f
        return ("fail", "RUNNING but no fresh heartbeat with progress before the deadline") if late else ("pending", "RUNNING, waiting for the harness heartbeat")
    if kind in ("resume_job", "rollback_last_good", "kill_process"):
        if kind == "kill_process" and not spec["params"].get("then_resume"):
            if res and res.get("ok"):
                mains = hbs[-1].procs if hbs else None
                if mains is not None and len(mains) <= 1:
                    return "ok", f"harness killed {res.get('detail', '')}; {len(mains)} entrypoint left"
            return ("fail", "process still present after deadline") if late else ("pending", "waiting for kill result")
        if (f := new_run_failed()):
            return "fail", f
        newrun = [h for h in hbs if h.run_id and h.run_id != base.get("run_id")]
        nsteps = [h.step for h in newrun if h.step is not None]
        if newrun and (len(nsteps) >= 2 and nsteps[-1] > nsteps[0] or (len(nsteps) >= 1 and nsteps[-1] > int(base.get("step") or 0))):   # progress within the NEW run
            if kind == "rollback_last_good":
                import math
                bad = [h for h in newrun[-3:] if h.loss is not None and (math.isnan(h.loss) or math.isinf(h.loss))]
                if bad:
                    return "fail", "loss non-finite again after rollback"
            return "ok", f"new run {newrun[-1].run_id} advancing: step {newrun[0].step}→{newrun[-1].step}"
        if res is None and late:
            return "fail", "harness never acknowledged the command (agent down or command rejected)"
        return ("fail", "new run did not start advancing before the deadline") if late else ("pending", "waiting for the resumed run to advance")
    if kind in ("clean_disk", "resize_disk"):
        need = float(spec["params"].get("min_free_gb", 5.0)); cur = hbs[-1].disk_avail_gb if hbs and hbs[-1].disk_avail_gb is not None else None
        freed = int(res.get("freed_bytes", 0)) if res else 0
        if cur is not None and cur >= need and (freed > 0 or base.get("disk_avail_gb") is None or cur > float(base["disk_avail_gb"])):
            return "ok", f"disk free {cur:.1f} GB (was {base.get('disk_avail_gb')}), freed {freed:,} B"
        if res and res.get("ok") and kind == "clean_disk" and int(res.get("freed_bytes", 0)) == 0:
            return "fail", "nothing eligible to clean (no local checkpoint has a verified copy in Storage)"
        return ("fail", f"disk free {cur} GB still below {need} GB at deadline") if late else ("pending", "waiting for the next heartbeat")
    if kind == "quarantine_artifact":
        if res and res.get("ok"):
            return "ok", f"quarantined {spec['params'].get('path', '')}"
        return ("fail", "harness did not confirm quarantine") if late else ("pending", "waiting for harness")
    return ("fail", f"no verifier for {kind}") if late else ("pending", "")


# ----------------------------------------------------------------------------- orchestration
def _event(inc, phase: str, **kw) -> None:
    print(_j.dumps({"event": "warden.recovery", "severity": "INFO", "phase": phase, "incident_id": inc.incident_id, "job": inc.job_id,
                    "attempt": inc.attempt, **kw}, default=str), flush=True)


def after_execute(inc, dec: Decision, r, notify=None) -> None:
    """Called right after ex.execute() by tick / pipeline / approvals. Moves the incident to VERIFYING with a spec, or to the next rung."""
    dec.status = DecisionStatus.DONE if r.ok else DecisionStatus.FAILED
    inc.attempt += 1
    if inc.state != S.EXECUTING:
        transition(inc, S.EXECUTING)
    if not r.ok:
        transition(inc, S.FAILED_ACTION, note=r.error[:200])
        db.decisions.put(dec); db.incidents.put(inc)
        _event(inc, "action_failed", action=dec.action.value, error=r.error[:160])
        advance(inc, notify, reason=f"{dec.action.value} failed: {r.error[:120]}")
        return
    job = db.jobs.get(inc.job_id) if inc.job_id else None
    inst = compute().describe(dec.params.get("instance_ref", "")) if dec.params.get("instance_ref") else None
    inc.verify = spec_for(dec, inst, job)
    transition(inc, S.VERIFYING, note=f"{dec.action.value}: {r.observed or 'done'} — verifying until {inc.verify['deadline'][11:16]} UTC")
    if dec.action == Action.NOTIFY:
        needs_human = bool(inc.ladder) or inc.severity == "critical" or bool((inc.diagnosis or {}).get("needs_human")) or (inc.diagnosis or {}).get("category") == "unknown"
        transition(inc, S.ESCALATED if needs_human else S.RESOLVED,
                   note="notified; a human is needed" + (" — remaining hypotheses: " + ", ".join(r["action"] for r in inc.ladder) if inc.ladder else "") if needs_human else "notified")
    db.decisions.put(dec); db.incidents.put(inc)
    _event(inc, "verifying", action=dec.action.value, deadline=inc.verify.get("deadline"))


def advance(inc, notify=None, reason: str = "") -> None:
    """Next hypothesis: pop the ladder, policy-evaluate, execute or await approval. Empty ladder → ESCALATED."""
    from warden.watcher.tick import _ctx_for, _is_frozen
    maxa = int(POLICY.get("recovery", {}).get("max_attempts_per_incident", 4))
    if inc.state == S.VERIFYING:
        transition(inc, S.FAILED_ACTION, note=reason[:200])
    if not inc.ladder or inc.attempt >= maxa:
        if inc.state in (S.FAILED_ACTION, S.VERIFYING, S.DECIDED):
            transition(inc, S.ESCALATED, note=("hypotheses exhausted" if not inc.ladder else f"{maxa} attempts reached") + f" — {reason}"[:200])
        db.incidents.put(inc); _event(inc, "escalated", reason=reason[:160])
        if notify: notify(inc, None, f"🆘 {inc.summary} — Warden tried {inc.attempt} recovery step(s); needs a human: {reason[:140]}")
        return
    rung = inc.ladder.pop(0)
    action = Action(rung["action"])
    job = db.jobs.get(inc.job_id) if inc.job_id else None
    inst = compute().describe(job.instance_ref if job and job.instance_ref else inc.instance_ref) if (job and job.instance_ref) or inc.instance_ref else None
    if inst and job and inst.ref != inc.instance_ref:
        inc.instance_ref = inst.ref
    ctx = _ctx_for(job, inst, action, _is_frozen())
    if action == Action.CHANGE_MACHINE_TYPE and inst:
        from warden.providers.gce import bigger_machine
        mt = rung["params"].get("machine_type") or bigger_machine(inst.machine_type)
        if mt and inst.hourly_price_usd:
            ctx.price_increase_pct = max(0.0, (compute().price_of(mt, inst.spot) / inst.hourly_price_usd - 1) * 100)
    if action == Action.RELOCATE_ZONE and inst and rung["params"].get("spot") is False and inst.hourly_price_usd:
        ctx.price_increase_pct = max(0.0, (compute().price_of(inst.machine_type, False) / inst.hourly_price_usd - 1) * 100)
    from warden.watcher.tick import _policy_for
    dec = policy_eval(action, ctx, _policy_for(job))
    dec.incident_id = inc.incident_id; dec.job_id = inc.job_id
    from warden.policy.engine import limit_events
    for _e in limit_events(dec):
        print(_j.dumps({"event": "warden.limit", "severity": "WARNING", "action": action.value, "incident_id": inc.incident_id, "job": inc.job_id, "limit": _e}), flush=True)
    dec.params = {"instance_ref": inst.ref if inst else inc.instance_ref, "run_id": job.run_id if job else "", **rung["params"], "reason": rung.get("why", "")}
    dec.explain = [f"hypothesis {inc.attempt + 1}: {rung.get('why', '')}"] + list(dec.explain)
    if action != Action.NOTIFY:
        dec.dry_run_plan = ex.dry_run(dec, compute())
    db.decisions.put(dec); inc.decision_ids.append(dec.decision_id)
    transition(inc, S.DECIDED, note=f"next hypothesis → {action}: {dec.verdict}")
    _event(inc, "next_hypothesis", action=action.value, verdict=str(dec.verdict), why=rung.get("why", ""))
    if dec.verdict == Verdict.AUTO:
        transition(inc, S.EXECUTING); dec.status = DecisionStatus.EXECUTING; db.decisions.put(dec)
        r = ex.execute(dec, compute())
        if r.ok and action != Action.NOTIFY:
            db.cost_add(now().strftime("%Y-%m-%d"), "auto_spend_usd", dec.cost_usd, inst.ref if inst else "")
        if notify: notify(inc, dec, f"{'🔁' if r.ok else '❌'} {inc.summary} → {action} ({dec.autonomy}, attempt {inc.attempt + 1}): {r.observed or r.error}")
        after_execute(inc, dec, r, notify)
    elif dec.verdict == Verdict.NEED_APPROVAL:
        transition(inc, S.AWAITING_APPROVAL); db.incidents.put(inc)
        if notify: notify(inc, dec, f"🟡 {inc.summary} → next hypothesis {action} needs approval: {rung.get('why', '')}")
    elif dec.verdict == Verdict.HELD:
        transition(inc, S.HELD); db.incidents.put(inc)
    else:
        db.incidents.put(inc)
        advance(inc, notify, reason=f"{action} denied by policy: {dec.explain[-1]}")


def process_verifying(notify=None) -> dict[str, Any]:
    """Tick hook: look at every VERIFYING incident and decide from the world."""
    out = {"checked": 0, "resolved": 0, "advanced": 0}
    for inc in db.incidents.list(state="VERIFYING", limit=50):
        if not inc.verify:
            continue
        out["checked"] += 1
        status, why = check(inc, inc.verify)
        inc.verify.setdefault("checks", []).append({"ts": now().isoformat(), "status": status, "why": why})
        inc.verify["checks"] = inc.verify["checks"][-20:]
        if status == "ok":
            inc.verify["result"] = "ok"
            transition(inc, S.RESOLVED, note=f"verified: {why}"[:200]); db.incidents.put(inc); out["resolved"] += 1
            _event(inc, "verified", why=why)
            if notify: notify(inc, None, f"✅ {inc.summary} — verified: {why}")
        elif status == "fail":
            inc.verify["result"] = "fail"; db.incidents.put(inc); out["advanced"] += 1
            _event(inc, "verify_failed", why=why)
            advance(inc, notify, reason=why)
        else:
            db.incidents.put(inc)
    return out
