"""Read models for the Warden UI (Jinja2). Everything the templates need is assembled here; templates contain no logic beyond loops."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from warden.store import firestore as db

STALE_HB_S = 180
STALE_HEALTH_S = 900
PERIODIC_SOURCES = {"watcher", "steward", "deadman", "compute_api"}
SOURCE_NAME = {"watcher": "Watcher", "steward": "Steward", "deadman": "Watchdog", "compute_api": "Compute Engine API", "gcs": "Cloud Storage",
               "gemini": "Gemini", "llm_budget": "LLM budget", "llm_circuit": "Gemini circuit breaker", "discord": "Discord", "verifier": "Verifier"}
STATE_LABEL = {"DETECTED": "Open", "TRIAGED": "Open", "DIAGNOSING": "Open", "DIAGNOSED": "Open", "DECIDED": "Open", "EXECUTING": "Executing", "VERIFYING": "Verifying",
               "RESOLVED": "Resolved", "AWAITING_APPROVAL": "Awaiting approval", "HELD": "Held", "ESCALATED": "Escalated", "FAILED_ACTION": "Escalated", "CLOSED": "Closed", "FALSE_POSITIVE": "Closed"}
STATE_CLS = {"Open": "crit", "Executing": "info", "Verifying": "info", "Resolved": "ok", "Awaiting approval": "warn", "Held": "grey", "Escalated": "crit", "Closed": "grey"}
DEC_LABEL = {"PENDING": "Pending", "APPROVED": "Approved", "REJECTED": "Rejected", "EXECUTING": "Executing", "DONE": "Done", "FAILED": "Failed", "EXPIRED": "Expired"}
VERDICT_LABEL = {"AUTO": "Automatic", "NEED_APPROVAL": "Approval required", "HELD": "Held", "DENY": "Denied"}
RADIUS_LABEL = {"none": "Nothing", "this_run": "This run only", "this_job": "This job only", "budget": "Budget", "artifacts": "Artifacts"}
ACTION_LABEL = {"notify": "Notify", "start_instance": "Start instance", "resume_job": "Resume job", "stop_instance": "Stop instance",
                "quarantine_artifact": "Quarantine artifact", "rollback_last_good": "Roll back to last good checkpoint", "relocate_zone": "Relocate zone",
                "resize_disk": "Resize disk", "kill_process": "Kill process", "resume_smaller_batch": "Resume with smaller batch", "change_machine_type": "Change machine type",
                "clean_disk": "Clean disk (checkpoints already in Storage)", "launch": "Launch job", "promote": "Promote autonomy", "demote": "Demote autonomy", "hold": "Hold job", "propose": "Propose action"}
ACTION_VERB = {"start_instance": "start", "stop_instance": "stop", "resume_job": "resume", "quarantine_artifact": "quarantine", "rollback_ckpt": "roll back", "kill_process": "kill", "notify": "notify"}
ACTION_CHANGE = {"start_instance": "TERMINATED → RUNNING", "stop_instance": "RUNNING → TERMINATED", "resume_job": "Job resumed from last verified checkpoint",
                 "quarantine_artifact": "Artifact renamed to .corrupt", "rollback_ckpt": "Checkpoint rolled back", "kill_process": "Process terminated", "notify": "Notification sent"}
ACTION_REVERSIBLE = {"start_instance": "Yes — instance can be stopped again", "stop_instance": "Yes — instance can be started again; disk retained",
                     "resume_job": "Yes — job can be stopped", "quarantine_artifact": "Yes — file is renamed, not deleted", "rollback_ckpt": "Yes — later checkpoints are kept",
                     "kill_process": "Partially — the run must be resumed", "notify": "Not applicable"}
# GCE calls a stopped machine TERMINATED; "Gone" is reserved for a machine that no longer exists at the provider.
INSTANCE_STATUS = {"RUNNING": ("Running", "ok"), "STARTING": ("Starting", "warn"), "STOPPING": ("Stopping", "warn"),
                   "TERMINATED": ("Stopped", "grey"), "STOPPED": ("Stopped", "grey"),
                   "DELETED": ("Gone", "grey"), "UNKNOWN": ("Unknown", "grey")}
JOB_STATUS = {"PENDING": ("Pending", "grey"), "RUNNING": ("Running", "ok"), "COMPLETE": ("Complete", "grey"), "FINISHED_UNVERIFIED": ("Finished, unverified", "warn"), "FAILED": ("Failed", "crit"), "STOPPED": ("Stopped", "grey")}
RULE_LABEL = {"stopped_external": "Instance stopped externally", "preempted": "Instance preempted", "orphan": "Orphan instance", "idle": "Idle instance",
              "fin_ok_pending_verify": "Run finished, verification pending", "artifact_unverified": "Artifact verification failed", "run_fin_nonzero": "Run exited with error",
              "marker_invalid": "Invalid marker", "done_without_exit": "DONE marker without exit code", "stuck": "Job stuck", "slow": "Job slow", "harness_dead": "Harness heartbeat lost",
              "disk_low": "Disk space low", "dup_process": "Duplicate process", "nan_loss": "Non-finite loss", "unsafe_config": "Unsafe instance configuration", "instance_missing": "Instance missing",
              "complete_running": "Job complete, instance still running", "preflight_fail": "Preflight failed", "smoke_invalid": "Smoke test incomplete", "budget_80": "Budget 80 % used",
              "budget_exhausted": "Budget exhausted", "throughput_drop": "Throughput dropped", "grad_spike": "Gradient spike", "plateau": "Loss plateau", "disk_trend": "Disk filling up", "vram_creep": "GPU memory rising"}
STEP_LABEL = {"DETECTED": "Detected", "TRIAGED": "Triaged", "DIAGNOSING": "Diagnosing", "DIAGNOSED": "Diagnosed", "DECIDED": "Decided", "AWAITING_APPROVAL": "Approval required",
              "EXECUTING": "Executing", "VERIFYING": "Verifying", "RESOLVED": "Resolved", "ESCALATED": "Escalated", "HELD": "Held", "FAILED_ACTION": "Action failed", "CLOSED": "Closed", "FALSE_POSITIVE": "False positive"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def _s(x: Any) -> str:
    if isinstance(x, bool):
        return "Yes" if x else "No"
    if isinstance(x, (int, float)):
        return f"{x:,}" if isinstance(x, int) else f"{x:.4g}"
    return str(x).split(".")[-1]


def iso(d: datetime | None) -> str:
    return d.astimezone(timezone.utc).isoformat() if d else ""


def usd(v: float, digits: int = 2) -> str:
    return f"${v:,.{digits}f}"


# Policy keys are configuration identifiers; an operator should read what the limit means, with the unit on the
# value where it belongs, not a de-underscored variable name ("Approval ttl minutes 30").
POLICY_LABEL = {
    "auto_spend_daily_cap_usd": ("Automatic spend, per day", "usd"),
    "approval_ttl_minutes": ("Approval expires after", "min"),
    "idle_grace_minutes": ("Idle before flagged", "min"),
    "orphan_grace_minutes": ("Orphan before flagged", "min"),
    "boot_grace_minutes": ("Grace after boot", "min"),
    "max_auto_actions_per_hour": ("Automatic actions, per hour", ""),
    "max_failed_verifications_in_row": ("Failed verifications before opening", ""),
    "open_minutes": ("Stays open for", "min"),
    "max_attempts_per_incident": ("Hypotheses per incident", ""),
    "streak": ("Approvals in a row to promote", ""),
}
LIMIT_LABEL = {"per_hour": "{v}/hour", "per_day": "{v}/day", "max_cost_usd": "max {money}",
               "max_price_increase_pct": "max +{v}% price"}


def _unit(v: Any, unit: str) -> str:
    if unit == "usd":
        try:
            return f"${float(v):,.2f}"
        except (TypeError, ValueError):
            return str(v)
    return f"{v} min" if unit == "min" else str(v)


def policy_pairs(d: dict) -> list[tuple[str, str]]:
    """Config keys → operator-readable label and a value that carries its own unit."""
    out = []
    for k, v in (d or {}).items():
        lab, unit = POLICY_LABEL.get(k, (k.replace("_", " ").capitalize(), ""))
        out.append((lab, _unit(v, unit)))
    return out


def limits_text(d: dict) -> str:
    if not d:
        return "—"
    out = []
    for k, v in d.items():
        try:
            money = f"${float(v):,.2f}"
        except (TypeError, ValueError):
            money = str(v)
        out.append(LIMIT_LABEL.get(k, k.replace("_", " ") + " {v}").format(v=v, money=money))
    return " · ".join(out)


def label(m: dict, k: Any) -> str:
    k = _s(k); return m.get(k, k.replace("_", " ").capitalize())


def age_s(d: datetime | None) -> float | None:
    return (now() - d).total_seconds() if d else None


def is_frozen() -> bool:
    d = db.client().collection("policies").document("runtime").get()
    return bool(d.exists and d.to_dict().get("frozen"))


def health_rows() -> list[dict]:
    out = []
    for d in db.client().collection("health").stream():
        r = d.to_dict() | {"src": d.id}
        fails = int(r.get("consecutive_failures", 0) or 0); last = r.get("last_ok_at")
        a = age_s(datetime.fromisoformat(last)) if last else None
        if r["src"] in PERIODIC_SOURCES:
            st, cls = ("Stale", "stale") if (a is None or a > STALE_HEALTH_S) else (("Healthy", "ok") if fails == 0 else ("Degraded", "warn"))
        else:
            st, cls = ("Healthy", "ok") if fails == 0 else ("Failing", "crit")
        out.append({"src": r["src"], "name": SOURCE_NAME.get(r["src"], r["src"]), "status": st, "cls": cls, "last_ok_iso": last or "", "fails": fails, "error": str(r.get("last_error", ""))[:160], "stats": r.get("stats")})
    return sorted(out, key=lambda x: x["name"])


def hb_state(h) -> tuple[str, str, str]:
    """(text, css, iso) for a heartbeat."""
    if not h:
        return "No heartbeat", "warn", ""
    a = age_s(h.ts)
    return ("Heartbeat", "ok", iso(h.ts)) if a is not None and a <= STALE_HB_S else ("Stale", "warn", iso(h.ts))


def why_sentence(inc, dec) -> str:
    if inc and inc.diagnosis.get("human_summary"):
        return str(inc.diagnosis["human_summary"])
    return inc.summary if inc else label(ACTION_LABEL, dec.action)


def decision_view(dec, inc, inst=None) -> dict:
    d = (inc.diagnosis if inc else {}) or {}; cc = (inc.crosscheck if inc else {}) or {}
    plan = dec.dry_run_plan.get("plan") if dec.dry_run_plan else None
    target = dec.params.get("instance_ref") or (inc.instance_ref if inc else "") or dec.job_id
    hourly = inst.hourly_price_usd if inst else (inc.cost_burning_usd_per_hour if inc else 0.0)
    if _s(dec.action) == "stop_instance":
        cost_impact = f"Saves {usd(hourly, 3)}/h · action {usd(dec.cost_usd, 2)}"
    elif _s(dec.action) == "start_instance":
        cost_impact = f"Adds {usd(hourly, 3)}/h · action {usd(dec.cost_usd, 2)}"
    else:
        cost_impact = f"Action {usd(dec.cost_usd, 2)}"
    exp = dec.expires_at
    return {"decision_id": dec.decision_id, "incident_id": dec.incident_id, "action": _s(dec.action), "action_label": label(ACTION_LABEL, dec.action),
            "verb": ACTION_VERB.get(_s(dec.action), "run"), "job_id": dec.job_id, "target": target, "target_short": target.split("/")[-1],
            "autonomy": _s(dec.autonomy), "radius": label(RADIUS_LABEL, dec.blast_radius), "why": why_sentence(inc, dec),
            "gemini": ({"conf": cc.get("adjusted_confidence", d.get("confidence")), "passed": bool(cc.get("passed")), "cost": usd(inc.llm_cost_usd, 3)} if d else None),
            "rule": label(RULE_LABEL, inc.rule) if inc else "", "expires_iso": iso(exp), "expired": bool(exp and exp < now()),
            "change": ACTION_CHANGE.get(_s(dec.action), "—"), "reversible": ACTION_REVERSIBLE.get(_s(dec.action), "—"), "cost_impact": cost_impact,
            "api": (plan or {}).get("api", "") if isinstance(plan, dict) else "", "explain": list(dec.explain), "status": label(DEC_LABEL, dec.status), "status_raw": _s(dec.status),
            "verdict": label(VERDICT_LABEL, dec.verdict), "created_iso": iso(dec.created_at), "approved_by": dec.approved_by,
            "result": dec.result or {}, "plan_rows": [(k.upper(), _s(v) if not isinstance(v, (dict, list)) else json.dumps(v)) for k, v in (plan or {}).items()] if isinstance(plan, dict) else []}


def incident_row(inc) -> dict:
    st = label(STATE_LABEL, inc.state)
    return {"id": inc.incident_id, "ref": "INC-" + inc.incident_id[-4:].upper(), "severity": inc.severity, "severity_label": inc.severity.capitalize(),
            "title": label(RULE_LABEL, inc.rule), "sub": inc.summary[:120], "job": inc.job_id or "—", "instance": inc.instance_ref, "state": st, "state_cls": STATE_CLS.get(st, "grey"),
            "opened_iso": iso(inc.created_at), "updated_iso": iso(inc.updated_at), "burn": usd(inc.cost_burning_usd_per_hour, 3), "llm": usd(inc.llm_cost_usd, 3), "rule": inc.rule}


def activity_rows(incs, decs, limit: int = 12) -> list[dict]:
    """Recent actions phrased as sentences with the actor that produced them."""
    out = []
    dec_by_id = {d.decision_id: d for d in decs}
    for inc in incs[:60]:
        title = label(RULE_LABEL, inc.rule); tgt = inc.instance_ref.split("/")[-1] if inc.instance_ref else inc.job_id
        for t in inc.timeline:
            actor = str(t.get("actor", "")); to = _s(t.get("to", "")); note = str(t.get("note", ""))
            if actor.startswith("human"):
                out.append({"ts": t.get("ts"), "actor": "Operator", "cls": "operator", "text": f"{(note[:1].upper() + note[1:])[:110]} — {title.lower()} on {tgt}", "inc": inc.incident_id, "ok": True})
            elif to in ("RESOLVED", "ESCALATED", "FAILED_ACTION", "AWAITING_APPROVAL", "HELD"):
                res = {"RESOLVED": "Resolved", "ESCALATED": "Escalated", "FAILED_ACTION": "Action failed", "AWAITING_APPROVAL": "Awaiting approval", "HELD": "Held"}[to]
                out.append({"ts": t.get("ts"), "actor": "Warden", "cls": "warden", "text": f"{title} on {tgt} · {res}" + (f" · {note}" if note and to not in ("RESOLVED",) else ""), "inc": inc.incident_id, "ok": to in ("RESOLVED", "AWAITING_APPROVAL", "HELD")})
        if inc.diagnosis:
            d = inc.diagnosis; cc = inc.crosscheck or {}
            out.append({"ts": inc.updated_at.isoformat(), "actor": "Gemini", "cls": "gemini",
                        "text": f"Diagnosed {title.lower()} on {tgt} as {d.get('category', '?')} · confidence {cc.get('adjusted_confidence', d.get('confidence', '?'))} · {usd(inc.llm_cost_usd, 3)}", "inc": inc.incident_id, "ok": bool(cc.get("passed", True))})
        for did in inc.decision_ids:
            dd = dec_by_id.get(did)
            if dd and dd.result and _s(dd.status) in ("DONE", "FAILED"):
                ok = _s(dd.status) == "DONE"
                out.append({"ts": dd.created_at.isoformat(), "actor": "Warden" if not dd.approved_by else "Operator", "cls": "warden" if not dd.approved_by else "operator",
                            "text": f"{label(ACTION_LABEL, dd.action)} on {tgt} · " + ("Verified" if ok else f"Failed: {dd.result.get('error', '')[:80]}"), "inc": inc.incident_id, "ok": ok})
    out.sort(key=lambda e: str(e["ts"]), reverse=True)
    return out[:limit]


def base_context() -> dict:
    """Shared by every page: counts for navigation, service status, frozen flag."""
    decs = sorted(db.decisions.list(limit=300), key=lambda x: x.created_at, reverse=True)
    incs = sorted(db.incidents.list(limit=300), key=lambda x: x.created_at, reverse=True)
    pending = [d for d in decs if _s(d.status) == "PENDING" and _s(d.verdict) == "NEED_APPROVAL"]
    open_incs = [i for i in incs if label(STATE_LABEL, i.state) not in ("Resolved", "Closed")]
    health = health_rows()
    services = [h for h in health if h["src"] in ("watcher", "steward", "deadman")]
    return {"now_iso": iso(now()), "frozen": is_frozen(), "decs": decs, "incs": incs, "pending": pending, "open_incs": open_incs, "health": health,
            "services": services, "n_pending": len(pending), "n_open": len(open_incs)}


def _needs_attention(rows: list[dict], keep: int = 8) -> list[dict]:
    """The overview is an inbox: show the jobs a human would look at first, and link to the rest."""
    def rank(r: dict) -> tuple:
        return (0 if r["hb_cls"] == "crit" else 1 if r["hb_cls"] == "warn" else 2,
                0 if r["status"] not in ("Complete", "Abandoned") else 1, r["job_id"])
    return sorted(rows, key=rank)[:keep]


def overview_context() -> dict:
    from warden.steward import ledger
    ctx = base_context()
    jobs = db.jobs.list(limit=200); insts = db.fleet.list(limit=200)
    inst_by_ref = {i.ref: i for i in insts}; inst_by_job = {i.job_id: i for i in insts if i.job_id}
    inc_by_id = {i.incident_id: i for i in ctx["incs"]}
    running = [i for i in insts if _s(i.status) == "RUNNING"]
    try:
        proj = ledger.projection()
    except Exception as e:  # noqa: BLE001
        proj = {"today_usd": 0.0, "month_to_date_usd": 0.0, "burn_usd_per_hour": 0.0, "runway_days": None, "cap_usd": 150.0, "if_left_running_30d_usd": 0.0, "error": str(e)[:80]}
    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    resolved_today = sum(1 for i in ctx["incs"] if label(STATE_LABEL, i.state) in ("Resolved", "Closed") and i.updated_at >= day_start)
    ettrs = {j.job_id: ledger.ettr(j.job_id, 168) for j in jobs}
    eff = sum(e.get("effective_h") or 0 for e in ettrs.values()); paid = sum(e.get("paid_h") or 0 for e in ettrs.values())
    decisions = []
    for d in ctx["pending"]:
        inc = inc_by_id.get(d.incident_id); ref = d.params.get("instance_ref") or (inc.instance_ref if inc else "")
        decisions.append(decision_view(d, inc, inst_by_ref.get(ref)))
    job_rows = []
    for j in sorted(jobs, key=lambda x: (_s(x.status) != "RUNNING", x.job_id)):
        h = db.last_heartbeat(j.job_id); txt, cls, ts = hb_state(h); st, scls = JOB_STATUS.get(_s(j.status), (_s(j.status), "grey"))
        total = int((j.expect or {}).get("steps") or 0)
        pct = 100 if _s(j.status) == "COMPLETE" else (min(100, int(100 * (h.step or 0) / total)) if (h and h.step and total) else None)
        if h and h.step is not None and not h.synthetic:
            line = f"Step {h.step:,}" + (f" of {total:,}" if total else "") + (f" · loss {h.loss:.3f}" if h.loss is not None else "")
        elif j.last_good_ckpt.get("path"):
            line = f"Last verified checkpoint {j.last_good_ckpt['path']}"
        elif h:
            line = f"CPU {h.cpu_pct or 0:.0f} % · disk {h.disk_avail_gb or 0:.1f} GB free"
        else:
            line = "—"
        e = ettrs.get(j.job_id) or {}
        job_rows.append({"job_id": j.job_id, "status": st, "status_cls": scls, "phase": j.phase, "pct": pct, "line": line, "hb_text": txt, "hb_cls": cls, "hb_iso": ts,
                         "mode": "Log parser" if j.legacy else "Instrumented", "ettr": e.get("ettr"), "eff_h": e.get("effective_h"), "paid_h": e.get("paid_h"),
                         "instance": inst_by_job.get(j.job_id), "run_id": j.run_id, "spent": usd(j.spent_usd, 3)})
    ctx.update({"decisions": decisions, "stats": {"open": len(ctx["open_incs"]), "resolved_today": resolved_today, "running": len(running), "instances": len(insts),
                                                   "burn": usd(proj["burn_usd_per_hour"], 3), "mtd": usd(proj["month_to_date_usd"]), "cap": usd(proj["cap_usd"], 0),
                                                   "cap_pct": max(1, round(100 * proj["month_to_date_usd"] / max(proj["cap_usd"], 1))) if proj["month_to_date_usd"] > 0 else 0,
                                                   "ettr_pct": round(100 * eff / paid) if paid else None, "eff_h": round(eff, 2), "paid_h": round(paid, 2), "today": usd(proj["today_usd"])},
                # Owner's call (30 Aug): incidents read newest first everywhere, so the order never changes between pages.
                "open_rows": [incident_row(i) for i in ctx["open_incs"][:8]],
                "jobs": job_rows, "jobs_shown": _needs_attention(job_rows), "activity": activity_rows(ctx["incs"], ctx["decs"], 6), "proj": proj})
    return ctx


def incident_context(incident_id: str) -> dict | None:
    ctx = base_context()
    inc = db.incidents.get(incident_id)
    if not inc:
        return None
    insts = {i.ref: i for i in db.fleet.list(limit=200)}
    row = incident_row(inc); d = inc.diagnosis or {}; cc = inc.crosscheck or {}
    decisions = [db.decisions.get(x) for x in inc.decision_ids]; decisions = [x for x in decisions if x]
    pending = [decision_view(x, inc, insts.get(x.params.get("instance_ref") or inc.instance_ref)) for x in decisions if _s(x.status) == "PENDING" and _s(x.verdict) == "NEED_APPROVAL"]
    past = [decision_view(x, inc, insts.get(x.params.get("instance_ref") or inc.instance_ref)) for x in decisions if not (_s(x.status) == "PENDING" and _s(x.verdict) == "NEED_APPROVAL")]
    evidence = []
    for eid in inc.evidence_ids:
        ev = db.evidence.get(eid)
        if not ev:
            continue
        kind = {"rule": "Rule evidence", "log_window": "Log excerpt", "artifact_check": "Artifact verification", "heartbeat": "Heartbeat evidence", "investigation": "Investigation (agent reasoning trace)", "image": "Training curves (image shown to Gemini)"}.get(ev.kind, ev.kind)
        if ev.kind == "investigation" and isinstance(ev.payload, dict):
            evidence.append({"kind": kind, "summary": ev.summary, "created_iso": iso(ev.created_at), "results": None, "rows": [],
                             "notes": ev.payload.get("notes", ""), "cost": usd(float(ev.payload.get("cost_usd", 0.0)), 4),
                             "tools": [{"tool": t.get("tool", ""), "args": json.dumps(t.get("args", {}), ensure_ascii=False)[:200], "preview": str(t.get("result_preview", ""))[:300]} for t in ev.payload.get("tool_calls", [])]})
            continue
        evidence.append({"kind": kind, "summary": ev.summary, "created_iso": iso(ev.created_at), "results": ev.payload.get("results") if isinstance(ev.payload, dict) else None,
                         "rows": [(k.replace("_", " ").capitalize(), _s(v) if not isinstance(v, (dict, list)) else json.dumps(v)[:200]) for k, v in (ev.payload.items() if isinstance(ev.payload, dict) and ev.kind != "artifact_check" else [])][:8]})
    hbs = sorted(db.recent_heartbeats(inc.job_id, 60), key=lambda h: h.ts) if inc.job_id else []
    contract = any((not h.synthetic) and h.loss is not None for h in hbs)
    chart = None
    if hbs:
        vals = [(h.step or 0) for h in hbs] if contract else [(h.cpu_pct or 0) for h in hbs]
        vmax = max(vals) or 1; n = len(vals)
        pts = " ".join(f"{40 + i * 580 / max(n - 1, 1):.0f},{110 - 80 * v / vmax:.0f}" for i, v in enumerate(vals))
        chart = {"points": pts, "label": "Step" if contract else "CPU %", "max": f"{vmax:,.0f}" if contract else "100%", "mid": f"{vmax / 2:,.0f}" if contract else "50%",
                 "t0": hbs[0].ts.isoformat(), "t1": hbs[len(hbs) // 2].ts.isoformat(), "t2": hbs[-1].ts.isoformat()}
    # decision rail steps derived from the timeline
    seen = [_s(t.get("to", "")) for t in inc.timeline]
    steps = [("Detected", "DETECTED" in seen or "TRIAGED" in seen), ("Diagnosed", "DIAGNOSED" in seen or bool(d)),
             ("Approval required", "AWAITING_APPROVAL" in seen), ("Execute", "EXECUTING" in seen), ("Verify", "RESOLVED" in seen or "VERIFYING" in seen)]
    if not d and "AWAITING_APPROVAL" not in seen:
        steps = [s for s in steps if s[0] not in ("Diagnosed", "Approval required")]
    current = label(STATE_LABEL, inc.state)
    rail = []
    for name, done in steps:
        cls = "done" if done else "todo"
        if name == "Approval required" and current == "Awaiting approval":
            cls = "current"
        if name == "Execute" and current == "Executing":
            cls = "current"
        if name == "Verify" and current == "Verifying":
            cls = "current"
        if name == "Verify" and current == "Escalated":
            cls = "failed"
        rail.append({"name": name, "cls": cls})
    tl = [{"ts": t.get("ts"), "from": label(STEP_LABEL, t.get("from", "")), "to": label(STEP_LABEL, t.get("to", "")), "note": t.get("note", ""), "actor": "Operator" if str(t.get("actor", "")).startswith("human") else "Warden"} for t in inc.timeline]
    v = inc.verify or {}
    checks = v.get("checks") or []
    recovery = {"attempt": inc.attempt, "kind": label(ACTION_LABEL, v.get("kind", "")) if v else "", "deadline_iso": v.get("deadline", ""), "result": v.get("result", ""),
                "last_check": (checks[-1] if checks else None), "checks": checks[-6:],
                "ladder": [{"action": label(ACTION_LABEL, r.get("action", "")), "why": r.get("why", ""), "params": ", ".join(f"{k} {val}" for k, val in (r.get("params") or {}).items())} for r in (inc.ladder or [])],
                "memory_ref": inc.memory_ref, "can_false_positive": _s(inc.state) in ("AWAITING_APPROVAL", "ESCALATED"),
                "hypotheses_done": [{"action": x["action_label"], "status": x["status"], "why": next((e[len("hypothesis "):] for e in x["explain"] if e.startswith("hypothesis ")), "")} for x in past if x["status_raw"] in ("DONE", "FAILED")]}
    ctx.update({"inc": row, "summary": d.get("human_summary") or inc.summary, "diag": d, "cc": cc, "llm": usd(inc.llm_cost_usd, 3), "pending": pending, "past": past, "evidence": evidence, "recovery": recovery,
                "chart": chart, "contract": contract, "rail": rail, "timeline": tl, "daily": usd(inc.cost_burning_usd_per_hour * 24),
                "detected_by": f"Rule {inc.rule}" + (f" · {inc.summary}" if inc.summary else ""), "proposed": (label(ACTION_LABEL, d.get("recommended_action")) if d else (pending[0]["action_label"] if pending else "—"))})
    return ctx


def job_context(job_id: str) -> dict | None:
    """Job detail (A/J/K): spec, live state, learned baselines, per-job policy, final report, incidents of this job."""
    from warden.steward import ledger
    ctx = base_context()
    j = db.jobs.get(job_id)
    if not j:
        return None
    inst = db.fleet.get(j.instance_ref) if j.instance_ref else None
    h = db.last_heartbeat(job_id); txt, cls, ts = hb_state(h); st, scls = JOB_STATUS.get(_s(j.status), (_s(j.status), "grey"))
    e = ledger.ettr(job_id, 168)
    b = db.client().collection("baselines").document(job_id).get(); baselines = b.to_dict() if b.exists else {}
    pol = db.job_policy(job_id)
    incs = sorted([i for i in db.incidents.list(job_id=job_id, limit=200)], key=lambda i: i.created_at, reverse=True)
    rep = j.report or {}
    spec_rows = [(k.replace("_", " ").capitalize(), (json.dumps(v) if isinstance(v, (dict, list)) else str(v))[:160]) for k, v in (j.spec or {}).items() if k not in ("env", "name")]
    hold_until = j.operator_hold_until.isoformat() if j.operator_hold_until and j.operator_hold_until > now() else ""
    ctx.update({"job": j, "status": st, "status_cls": scls, "inst": inst, "hb_text": txt, "hb_cls": cls, "hb_iso": ts, "h": h, "ettr": e, "baselines": baselines, "policy": pol,
                "overrides": [(label(ACTION_LABEL, a), l) for a, l in (j.autonomy_overrides or {}).items()], "spec_rows": spec_rows, "report": rep,
                "report_rows": [(k.replace("_", " ").capitalize(), (json.dumps(v) if isinstance(v, (dict, list)) else str(v))[:200]) for k, v in rep.items() if k not in ("artifacts", "incidents")],
                "incidents": [incident_row(i) for i in incs[:30]], "hold_until": hold_until, "spent": usd(j.spent_usd, 3), "cap": usd(j.budget_cap_usd, 2) if j.budget_cap_usd else "—",
                "hb_rows": [("Phase", j.phase or "—"), ("Step", f"{h.step:,}" if h and h.step is not None else "—"), ("Loss", f"{h.loss:.4g}" if h and h.loss is not None else "—"),
                            ("Step rate", f"{h.step_per_s:.3g} /s" if h and h.step_per_s else "—"), ("Disk free", f"{h.disk_avail_gb:.1f} GB" if h and h.disk_avail_gb is not None else "—"),
                            ("GPU", f"{h.gpu_util:.0f} % · {h.vram_used_mb:.0f}/{h.vram_total_mb:.0f} MB" if h and h.gpu_util is not None and h.vram_total_mb else "—")]})
    return ctx
