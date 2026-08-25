"""Satu siklus Watcher: kumpulkan fakta → aturan → insiden (dedupe) → keputusan deterministik → eksekusi/izin.
Menulis denyut Warden di jalur SUKSES (P4). LLM hanya untuk temuan needs_llm (dipanggil pipeline, bukan di sini)."""
from __future__ import annotations

import json as _j

from datetime import timedelta
from typing import Any

from warden.config import settings
from warden.core.models import (Action, Decision, DecisionStatus, Evidence, Incident, IncidentState as S, InstanceStatus,
                                JobStatus, Verdict, now)
from warden.core.state_machine import transition
from warden.executor import registry as ex
from warden.policy.engine import Ctx, evaluate as policy_eval, load_policy
from warden.providers.registry import compute
from warden.store import firestore as db
from warden.watcher.rules import Facts, Finding, evaluate as rules_eval

POLICY = load_policy()
_prev_status: dict[str, InstanceStatus] = {}
SUGGEST = {"start_instance": Action.START_INSTANCE, "stop_instance": Action.STOP_INSTANCE, "resume_job": Action.RESUME_JOB,
           "kill_process": Action.KILL_PROCESS, "notify": Action.NOTIFY, "clean_disk": Action.CLEAN_DISK, "relocate_zone": Action.RELOCATE_ZONE}


def _dedupe_recent(key: str, minutes: int = 30) -> bool:
    for inc in db.incidents.list(dedupe_key=key, limit=5):
        if inc.state not in (S.RESOLVED, S.CLOSED, S.FALSE_POSITIVE) or (now() - inc.updated_at) < timedelta(minutes=minutes):
            return True
    return False


def _facts_for(inst, job, t) -> Facts:
    hb = db.last_heartbeat(job.job_id) if job else None
    hbs = db.recent_heartbeats(job.job_id, 30) if job else []
    run_fin = db.get_marker(job.job_id, job.run_id, "RUN_FIN") if job and job.run_id else None
    done_legacy = db.get_marker(job.job_id, job.run_id, "DONE_LEGACY") if job and job.run_id else None
    preflight = db.get_marker(job.job_id, "", "PREFLIGHT_FAIL") if job else None
    if preflight and inst and preflight.boot_id and preflight.boot_id != inst.boot_id:
        preflight = None                                   # belongs to an earlier boot
    smoke = db.get_marker(job.job_id, job.run_id, "SMOKE_FIN") if job and job.run_id else None
    ev = compute().preempt_events(inst.ref) if inst and inst.status == InstanceStatus.TERMINATED else []
    prev = _prev_status.get(inst.ref) if inst else None
    boot_age = 999.0
    if inst and inst.last_seen and hb and hb.boot_id == inst.boot_id:
        boot_age = max(0.0, (t - min(h.ts for h in hbs if h.boot_id == inst.boot_id)).total_seconds() / 60) if hbs else 999.0
    return Facts(t=t, inst=inst, job=job, hb=hb, hbs=hbs, run_fin=run_fin, done_legacy=done_legacy, preempt_events=ev,
                 preflight_fail=preflight, smoke_fin=smoke,
                 prev_status=prev, in_ledger=bool(job), boot_age_min=boot_age, policy=POLICY)


def _policy_for(job) -> dict:
    """Global policy with per-job overrides merged (G5): policies/<job_id> {autonomy: {...}, limits: {...}}."""
    import copy
    pol = copy.deepcopy(POLICY)
    jp = db.job_policy(job.job_id) if job else {}
    for k in ("autonomy", "limits", "global", "circuit_breaker"):
        if isinstance(jp.get(k), dict):
            pol[k] = {**pol.get(k, {}), **jp[k]}
    return pol


def _ctx_for(job, inst, action: Action, frozen: bool) -> Ctx:
    today = db.cost_today()
    hour_ago = now() - timedelta(hours=1)
    recent = [d for d in db.decisions.list(job_id=job.job_id if job else "", limit=200) if d.created_at > now() - timedelta(days=1)]
    same = [d for d in recent if d.action == action and d.status in (DecisionStatus.DONE, DecisionStatus.EXECUTING)]
    auto_hr = [d for d in recent if d.verdict == Verdict.AUTO and d.created_at > hour_ago and d.status != DecisionStatus.PENDING and d.action != Action.NOTIFY]   # notify tidak mengubah keadaan → tidak dihitung breaker
    failed_row = 0
    for d in sorted(recent, key=lambda d: d.created_at, reverse=True):
        if d.status == DecisionStatus.FAILED:
            failed_row += 1
        elif d.status == DecisionStatus.DONE:
            break
    hb = db.last_heartbeat(job.job_id) if job else None
    return Ctx(job_id=job.job_id if job else "", instance_ref=inst.ref if inst else "",
               hourly_price_usd=inst.hourly_price_usd if inst else 0.0,
               action_cost_usd=(inst.hourly_price_usd if inst and action == Action.START_INSTANCE else 0.0),
               actions_last_hour=len([d for d in same if d.created_at > hour_ago]), actions_today=len(same),
               auto_actions_last_hour=len(auto_hr), failed_verifications_in_row=failed_row,
               auto_spend_today_usd=float(today.get("auto_spend_usd", 0.0)),
               operator_hold_until=job.operator_hold_until if job else None,
               operator_active=bool(hb and hb.operator_active),
               stock_ok=True, boot_disk_auto_delete=inst.boot_disk_auto_delete if inst else None,
               managed=inst.managed if inst else True, legacy_job=job.legacy if job else False,
               autonomy_overrides=job.autonomy_overrides if job else {}, frozen=frozen)


def _is_frozen() -> bool:
    d = db.client().collection("policies").document("runtime").get()
    return bool(d.exists and d.to_dict().get("frozen"))


def _safe_notify(notify):
    """Notification failures (Discord down, network) never block or abort a tick — they become a health record."""
    if notify is None:
        return None
    def _n(inc, dec, text):
        try:
            notify(inc, dec, text)
        except Exception as e:  # noqa: BLE001
            db.health("notify", False, str(e)[:200])
    return _n


def run_tick(notify=None) -> dict[str, Any]:
    notify = _safe_notify(notify)
    """notify(incident, decision, text) → kartu Discord (disuntik supaya tick bisa diuji tanpa jaringan)."""
    t = now()
    stats = {"instances": 0, "findings": 0, "incidents_new": 0, "auto": 0, "approval": 0, "denied": 0, "held": 0, "errors": []}
    frozen = _is_frozen()
    try:
        instances = compute().list_instances()
        db.health("compute_api", True)
    except Exception as e:
        db.health("compute_api", False, str(e)[:200]); stats["errors"].append(f"compute: {e}")
        instances = []
    jobs = {j.job_id: j for j in db.jobs.list(limit=500)}
    seen_jobs: set[str] = set()
    for inst in instances:
        stats["instances"] += 1
        if not inst.managed:
            continue
        db.fleet.put(inst)
        job = jobs.get(inst.job_id) if inst.job_id else next((j for j in jobs.values() if j.instance_ref == inst.ref), None)
        if job:
            seen_jobs.add(job.job_id)
        facts = _facts_for(inst, job, t)
        findings = rules_eval(facts)
        _prev_status[inst.ref] = inst.status
        for f in findings:
            stats["findings"] += 1
            _handle(f, inst, job, frozen, stats, notify)
    # job RUNNING yang mesinnya tak terlihat (mesin hilang/dihapus dari luar)
    for job in jobs.values():
        if job.status == JobStatus.RUNNING and job.job_id not in seen_jobs and job.instance_ref:
            f = Finding("instance_missing", "critical", f"job {job.job_id}: instance {job.instance_ref} not in instance list",
                        f"missing:{job.instance_ref}", suggested_action="notify")
            _handle(f, None, job, frozen, stats, notify)
    db.heartbeat_self("watcher", {"tick_ms": int((now() - t).total_seconds() * 1000), "stats": {k: v for k, v in stats.items() if k != "errors"}})
    import json as _j, sys as _s
    print(_j.dumps({"event": "warden.heartbeat", "severity": "INFO", "tick_ms": int((now() - t).total_seconds() * 1000),
                    "instances": stats["instances"], "findings": stats["findings"], "auto": stats["auto"], "approval": stats["approval"]}), file=_s.stdout, flush=True)
    return stats


def _dismissed_before(job_id: str, rule: str, days: int = 7) -> int:
    """How many times a human marked this rule on this job as a false positive recently (memory lowers the weight, F3)."""
    if not job_id:
        return 0
    cutoff = now() - timedelta(days=days)
    return sum(1 for i in db.incidents.list(job_id=job_id, rule=rule, limit=50) if i.state == S.FALSE_POSITIVE and i.updated_at > cutoff)


def _handle(f: Finding, inst, job, frozen: bool, stats: dict, notify) -> None:
    if _dedupe_recent(f.dedupe_key):
        return
    n_fp = _dismissed_before(job.job_id if job else "", f.rule)
    if n_fp >= 2 and f.suggested_action not in ("", "notify", "verify") and f.severity != "critical":
        f.suggested_action = "notify"; f.severity = "info"; f.needs_llm = False
        f.summary = f"{f.summary} — proposed action withheld: dismissed as false positive {n_fp}× in 7 days"
    inc = Incident(job_id=job.job_id if job else "", instance_ref=inst.ref if inst else "", dedupe_key=f.dedupe_key,
                   rule=f.rule, severity=f.severity, summary=f.summary,
                   cost_burning_usd_per_hour=(inst.hourly_price_usd if inst and inst.status == InstanceStatus.RUNNING else 0.0))
    ev = Evidence(incident_id=inc.incident_id, kind="rule", summary=f.summary, payload=f.evidence)
    db.evidence.put(ev); inc.evidence_ids.append(ev.evidence_id)
    transition(inc, S.TRIAGED, note=f"rule {f.rule}")
    stats["incidents_new"] += 1
    _lag = None
    if inst is not None and getattr(inst, "last_stop_at", None):
        _lag = int((now() - inst.last_stop_at).total_seconds() * 1000)
    print(_j.dumps({"event": "warden.incident", "severity": "INFO", "phase": "opened", "rule": f.rule, "sev": f.severity, "job": job.job_id if job else "", "incident_id": inc.incident_id, "detect_ms": _lag}), flush=True)
    if f.suggested_action == "verify":
        db.incidents.put(inc); stats["incidents_new"] += 0
        return
    if f.needs_llm:
        # diserahkan ke pipeline LLM (Fase 4): tandai DIAGNOSING, pipeline yang melanjutkan
        transition(inc, S.DIAGNOSING, note="LLM diagnosis required"); db.incidents.put(inc)
        if notify:
            notify(inc, None, f"🔎 {f.summary} — diagnosing")
        return
    action = SUGGEST.get(f.suggested_action, Action.NOTIFY)
    from warden.executor import recovery
    first = (action.value, dict(f.action_params)) if action != Action.NOTIFY else None
    inc.ladder = recovery.build_ladder(f.rule, first)
    mem, ref = recovery.remembered_rung(inc.job_id, f.rule)
    if mem and inc.ladder and (mem["action"], mem["params"]) != (inc.ladder[0]["action"], inc.ladder[0]["params"]):
        inc.ladder.insert(0, mem); inc.memory_ref = ref
    if inc.ladder:
        rung = inc.ladder.pop(0); action = Action(rung["action"]); f.action_params = {**f.action_params, **rung["params"]}
    dec = policy_eval(action, _ctx_for(job, inst, action, frozen), _policy_for(job))
    dec.incident_id = inc.incident_id
    dec.params = {"instance_ref": inst.ref if inst else "", "run_id": job.run_id if job else "", **f.action_params}
    if inc.memory_ref:
        dec.explain.insert(0, f"memory: same pattern as {inc.memory_ref}")
    if action != Action.NOTIFY:
        dec.dry_run_plan = ex.dry_run(dec, compute())
    db.decisions.put(dec); inc.decision_ids.append(dec.decision_id)
    transition(inc, S.DECIDED, note=f"{action}: {dec.verdict}")
    print(_j.dumps({"event": "warden.decision", "severity": "INFO", "action": str(action), "verdict": str(dec.verdict), "autonomy": str(dec.autonomy), "incident_id": inc.incident_id, "decision_ms": int((now() - inc.created_at).total_seconds() * 1000)}), flush=True)
    from warden.policy.engine import limit_events
    for _e in limit_events(dec):
        print(_j.dumps({"event": "warden.limit", "severity": "WARNING", "action": str(action), "incident_id": inc.incident_id, "job": inc.job_id, "limit": _e}), flush=True)
    if dec.verdict == Verdict.AUTO:
        stats["auto"] += 1
        transition(inc, S.EXECUTING); dec.status = DecisionStatus.EXECUTING; db.decisions.put(dec)
        r = ex.execute(dec, compute())
        if r.ok and action != Action.NOTIFY:
            db.cost_add(now().strftime("%Y-%m-%d"), "auto_spend_usd", dec.cost_usd, inst.ref if inst else "")
        if notify:
            notify(inc, dec, f"{'✅' if r.ok else '❌'} {f.summary} → {action} ({dec.autonomy}): {r.observed or r.error}")
        from warden.executor import recovery
        recovery.after_execute(inc, dec, r, notify)      # VERIFYING with a world-check, or next hypothesis
        db.decisions.put(dec); db.incidents.put(inc)
        return
    elif dec.verdict == Verdict.NEED_APPROVAL:
        stats["approval"] += 1
        transition(inc, S.AWAITING_APPROVAL)
        if notify:
            notify(inc, dec, f"🟡 {f.summary} → proposed {action}; approval required (expires {dec.expires_at:%H:%M} UTC)")
    elif dec.verdict == Verdict.HELD:
        stats["held"] += 1
        transition(inc, S.HELD)
        if notify:
            notify(inc, dec, f"⏸ {f.summary} → held: {dec.explain[-1]}")
    else:
        stats["denied"] += 1
        transition(inc, S.ESCALATED, note="denied by policy")
        if notify:
            notify(inc, dec, f"⛔ {f.summary} → {action} denied: {dec.explain[-1]}")
    db.decisions.put(dec); db.incidents.put(inc)
