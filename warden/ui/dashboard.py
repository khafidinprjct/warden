"""warden-ui (Phase 8): NiceGUI dashboard. Data source: Firestore (page reload every 30 s). Actions go through warden-core (HMAC).
Pages: / (Overview) · /fleet · /jobs · /incidents · /incidents/{id} · /approvals · /policies · /budget · /audit · /health.

Style guide (binding): English product vocabulary (Incident, Decision, Autonomy Level, Blast Radius, Heartbeat, Watchdog);
headings Title Case, field labels UPPERCASE; structured label–value rows instead of concatenated sentences; times as
"25 Aug 2026, 18:51 WIB" plus a relative age; money "$0.034/h"; no explanatory copy on screen."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from nicegui import ui

from warden.signals.ingest import sign
from warden.store import firestore as db

CORE = os.environ.get("WARDEN_CORE_URL", "http://127.0.0.1:18090")
WIB = timezone(timedelta(hours=7))
STALE_HB_S = 180
STALE_HEALTH_S = 900
RELOAD_S = 30

CSS = """
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root { --bg:#0e1217; --panel:#10161d; --panel2:#0b0f14; --line:#1f2733; --line2:#161d26; --text:#e3e9f0; --muted:#8797ab; --dim:#5f6f83;
        --accent:#7cc4ff; --ok:#3fb950; --warn:#d29922; --crit:#f85149; --llm:#6b46c1; --det:#2f3f52; }
body, .q-page, .q-layout, .q-page-container { background: var(--bg) !important; color: var(--text); font-family: "IBM Plex Sans","Segoe UI",system-ui,sans-serif; font-size: 13px; }
.q-drawer { background: var(--panel2) !important; border-right: 1px solid var(--line); }
.q-header { background: var(--bg) !important; border-bottom: 1px solid var(--line); }
.mono { font-family: "IBM Plex Mono","SFMono-Regular",Menlo,monospace; }
.num { font-variant-numeric: tabular-nums; }
.w-card { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
.w-card-warn { background: #12130f; border: 1px solid var(--warn); border-radius: 8px; }
.w-head { display:flex; align-items:center; justify-content:space-between; gap: 8px; padding: 10px 14px; border-bottom: 1px solid var(--line); font-weight: 600; font-size: 14px; }
.w-row { border-bottom: 1px solid var(--line2); }
.w-row:last-child { border-bottom: none; }
.w-muted { color: var(--muted); } .w-dim { color: var(--dim); } .w-ok { color: var(--ok); } .w-warn { color: var(--warn); } .w-crit { color: var(--crit); }
.w-tag { display:inline-flex; align-items:center; padding: 2px 7px; border-radius: 4px; font-size: 10px; font-weight: 700; letter-spacing: .06em; white-space: nowrap; line-height: 1.5; text-transform: uppercase; }
.t-det { background: var(--det); color: #cfdbe8; } .t-llm { background: var(--llm); color: #f1eaff; } .t-ok { background:#0f1f17; color: var(--ok); }
.t-warn { background:#2a220e; color: var(--warn); } .t-crit { background:#3d1d1f; color: var(--crit); } .t-grey { background:#17212d; color:#aab6c4; }
.w-kpi { padding: 12px 14px; display:flex; flex-direction:column; gap: 3px; min-width: 0; }
.w-kpi .k { font-size: 11px; color: var(--muted); letter-spacing: .08em; text-transform: uppercase; } .w-kpi .v { font-size: 26px; font-weight: 600; line-height: 1.1; } .w-kpi .s { font-size: 11px; color: var(--dim); }
.w-nav a { display:flex; align-items:center; justify-content:space-between; padding: 8px 10px; border-radius: 6px; color:#aab6c4; text-decoration:none; font-size: 13px; }
.w-nav a.active { background:#17212d; color: var(--text); font-weight: 600; }
.w-nav .sec { font-size: 10px; letter-spacing: .12em; color: var(--dim); padding: 10px 8px 4px; text-transform: uppercase; }
.w-code { background: var(--bg); border-radius: 6px; padding: 8px 10px; font-size: 11px; color:#aab6c4; white-space: pre-wrap; word-break: break-word; }
.w-log { background:#1c1214; color:#f0a8a5; }
.w-lbl { font-size: 10px; letter-spacing: .1em; color: var(--dim); text-transform: uppercase; }
.w-kv { display:grid; grid-template-columns: 130px minmax(0,1fr); gap: 4px 12px; font-size: 12px; }
.w-kv .k { color: var(--dim); text-transform: uppercase; letter-spacing: .06em; font-size: 10px; padding-top: 2px; } .w-kv .v { color: var(--text); min-width: 0; overflow-wrap: anywhere; }
.w-link { color: var(--text); text-decoration: none; } .w-link:hover { color: var(--accent); }
.w-th { font-size: 10px; letter-spacing: .08em; color: var(--dim); text-transform: uppercase; }
a { color: var(--accent); }
.w-act { grid-template-columns: 72px 104px minmax(0,1fr) 18px; } .w-tl { grid-template-columns: 72px 170px minmax(0,1fr) 110px; }
@media (max-width: 1023px) { .w-order-first { order: -1; } }
@media (max-width: 640px) { .w-kpis { grid-template-columns: repeat(2, minmax(0,1fr)) !important; }
  .w-act { grid-template-columns: 64px minmax(0,1fr) 18px; } .w-act > :nth-child(2) { grid-column: 2; } .w-act > :nth-child(3) { grid-column: 1 / 3; } .w-act > :nth-child(4) { grid-column: 3; grid-row: 1; }
  .w-tl { grid-template-columns: 64px minmax(0,1fr); } .w-tl > :nth-child(3), .w-tl > :nth-child(4) { grid-column: 1 / 3; }
  .w-kv { grid-template-columns: 100px minmax(0,1fr); } }
</style>
"""

SOURCE_NAME = {"watcher": "Watcher", "steward": "Steward", "deadman": "Deadman Watchdog", "compute_api": "Compute Engine API", "gcs": "Cloud Storage",
               "gemini": "Gemini", "llm_budget": "LLM Budget", "llm_circuit": "Gemini Circuit Breaker", "discord": "Discord", "verifier": "Verifier"}
PERIODIC_SOURCES = {"watcher", "steward", "deadman", "compute_api"}
STATE_LABEL = {"DETECTED": "Detected", "TRIAGED": "Triaged", "DIAGNOSING": "Diagnosing", "DIAGNOSED": "Diagnosed", "DECIDED": "Decided", "EXECUTING": "Executing",
               "VERIFYING": "Verifying", "RESOLVED": "Resolved", "AWAITING_APPROVAL": "Awaiting Approval", "HELD": "Held", "ESCALATED": "Escalated",
               "FAILED_ACTION": "Action Failed", "CLOSED": "Closed", "FALSE_POSITIVE": "False Positive"}
DEC_LABEL = {"PENDING": "Pending", "APPROVED": "Approved", "REJECTED": "Rejected", "EXECUTING": "Executing", "DONE": "Done", "FAILED": "Failed", "EXPIRED": "Expired"}
VERDICT_LABEL = {"AUTO": "Automatic", "NEED_APPROVAL": "Approval Required", "HELD": "Held", "DENY": "Denied"}
RADIUS_LABEL = {"none": "None", "this_run": "This run", "this_job": "This job", "budget": "Budget", "artifacts": "Artifacts"}
ACTION_LABEL = {"notify": "Notify", "start_instance": "Start instance", "resume_job": "Resume job", "stop_instance": "Stop instance",
                "quarantine_artifact": "Quarantine artifact", "rollback_ckpt": "Roll back checkpoint", "relocate_zone": "Relocate zone",
                "resize_disk": "Resize disk", "kill_process": "Kill process", "resume_smaller_batch": "Resume with smaller batch",
                "change_machine_type": "Change machine type"}
JOB_STATUS_LABEL = {"PENDING": "Pending", "RUNNING": "Running", "COMPLETE": "Complete", "FINISHED_UNVERIFIED": "Finished, unverified", "FAILED": "Failed", "STOPPED": "Stopped"}
RULE_LABEL = {"stopped_external": "Instance stopped externally", "preempted": "Instance preempted", "orphan": "Orphan instance", "idle": "Idle instance",
              "fin_ok_pending_verify": "Run finished, verification pending", "artifact_unverified": "Artifact verification failed", "run_fin_nonzero": "Run exited with error",
              "marker_invalid": "Invalid marker", "done_without_exit": "DONE marker without exit code", "stuck": "Job stuck", "slow": "Job slow", "harness_dead": "Harness heartbeat lost",
              "disk_low": "Disk space low", "dup_process": "Duplicate process", "nan_loss": "Non-finite loss", "unsafe_config": "Unsafe instance configuration",
              "instance_missing": "Instance missing"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(x: Any) -> datetime | None:
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(x))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def rel(x: Any) -> str:
    d = _dt(x)
    if not d:
        return "—"
    s = (_now() - d).total_seconds()
    future = s < 0
    s = abs(s)
    txt = f"{int(s)} s" if s < 90 else f"{int(s / 60)} min" if s < 5400 else f"{s / 3600:.1f} h" if s < 172800 else f"{s / 86400:.0f} d"
    return f"in {txt}" if future else f"{txt} ago"


def wib(x: Any, fmt: str = "%H:%M:%S") -> str:
    d = _dt(x)
    return d.astimezone(WIB).strftime(fmt) if d else "—"


def when(x: Any) -> str:
    return f"{wib(x, '%d %b %Y, %H:%M')} WIB ({rel(x)})" if _dt(x) else "—"


def age_s(x: Any) -> float | None:
    d = _dt(x)
    return (_now() - d).total_seconds() if d else None


def usd(v: float, digits: int = 2) -> str:
    return f"${v:,.{digits}f}"


def _s(x: Any) -> str:
    """Enum/str to its bare name; numbers and booleans are formatted, never split."""
    if isinstance(x, bool):
        return "Yes" if x else "No"
    if isinstance(x, (int, float)):
        return f"{x:,}" if isinstance(x, int) else f"{x:.4g}"
    return str(x).split(".")[-1]


def label(mapping: dict, key: Any) -> str:
    k = _s(key)
    return mapping.get(k, k.replace("_", " ").capitalize())


def core(path: str, key: bytes | None = None, **params) -> dict:
    try:
        r = httpx.post(f"{CORE}{path}", params=params, headers={"X-Warden-Signature": sign(key or b"")}, timeout=30)
        try:
            return r.json()
        except ValueError:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:160]}


def notify_result(r: dict) -> None:
    if r.get("ok"):
        ui.notify(str(r.get("observed") or r.get("override") or r.get("verdict") or "Done"), type="positive")
    else:
        ui.notify(str(r.get("error") or r.get("detail") or "Failed"), type="negative")


def act(path: str, key: bytes, **params):
    notify_result(core(path, key, **params))
    ui.timer(0.9, ui.navigate.reload, once=True)


def is_frozen() -> bool:
    d = db.client().collection("policies").document("runtime").get()
    return bool(d.exists and d.to_dict().get("frozen"))


def tag(text: str, kind: str = "grey"):
    return ui.html(f'<span class="w-tag t-{kind}">{text}</span>')


def sev_kind(sev: str) -> str:
    return {"critical": "crit", "warning": "warn", "info": "ok"}.get(sev, "grey")


def state_kind(state: str) -> str:
    return {"RESOLVED": "ok", "CLOSED": "grey", "AWAITING_APPROVAL": "warn", "HELD": "grey", "ESCALATED": "crit", "FAILED_ACTION": "crit",
            "EXECUTING": "det", "VERIFYING": "det", "DIAGNOSING": "llm", "DIAGNOSED": "llm", "DETECTED": "warn", "TRIAGED": "warn", "DECIDED": "det"}.get(state, "grey")


def kv(pairs: list[tuple[str, Any]]):
    with ui.element("div").classes("w-kv"):
        for k, v in pairs:
            ui.html(f'<div class="k">{k}</div>')
            with ui.element("div").classes("v num"):
                if callable(v):
                    v()
                else:
                    ui.label(str(v))


def plan_rows(plan: dict) -> list[tuple[str, Any]]:
    if not isinstance(plan, dict):
        return [("PLAN", str(plan))]
    names = {"api": "API CALL", "zone": "ZONE", "instance": "INSTANCE", "from": "FROM", "to": "TO", "items": "METADATA", "channel": "CHANNEL", "cmd": "COMMAND", "path": "PATH"}
    return [(names.get(k, k.upper()), json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else _s(v)) for k, v in plan.items()]


def load_all() -> dict[str, Any]:
    from warden.steward import ledger
    jobs = db.jobs.list(limit=200)
    insts = db.fleet.list(limit=200)
    incs = sorted(db.incidents.list(limit=300), key=lambda x: x.created_at, reverse=True)
    decs = sorted(db.decisions.list(limit=300), key=lambda x: x.created_at, reverse=True)
    pending = [d for d in decs if _s(d.status) == "PENDING" and _s(d.verdict) == "NEED_APPROVAL"]
    hb = {j.job_id: db.last_heartbeat(j.job_id) for j in jobs}
    try:
        proj = ledger.projection()
    except Exception as e:  # noqa: BLE001
        proj = {"today_usd": 0.0, "month_to_date_usd": 0.0, "burn_usd_per_hour": 0.0, "runway_days": None, "cap_usd": 150.0, "if_left_running_30d_usd": 0.0, "error": str(e)[:80]}
    health = [d.to_dict() | {"src": d.id} for d in db.client().collection("health").stream()]
    return {"jobs": jobs, "insts": insts, "incs": incs, "decs": decs, "pending": pending, "hb": hb, "proj": proj, "health": health,
            "today": db.cost_today(), "frozen": is_frozen()}


def activity(incs, decs, limit: int = 40) -> list[dict]:
    dec_by_id = {d.decision_id: d for d in decs}
    out: list[dict] = []
    for inc in incs[:60]:
        title = f"{label(RULE_LABEL, inc.rule)} — {inc.job_id or inc.instance_ref}"
        for i, t in enumerate(inc.timeline):
            note = t.get("note", "") or ""
            actor = t.get("actor", "") or ""
            to = _s(t.get("to", ""))
            if actor.startswith("human"):
                lab, kind = "Operator", "ok"
            elif to == "DIAGNOSING":
                lab, kind = "Evidence", "det"
            elif to == "DECIDED" or "NEED_APPROVAL" in note or note.startswith("re-evaluated"):
                lab, kind = "Policy", "det"
            elif to == "EXECUTING":
                lab, kind = "Execution", "det"
            elif to in ("VERIFYING", "RESOLVED") and i > 0:
                lab, kind = "Verification", "det"
            else:
                lab, kind = "Rule", "det"
            out.append({"ts": t.get("ts"), "kind": kind, "label": lab, "title": title, "detail": note[:220], "inc": inc.incident_id,
                        "ok": to not in ("ESCALATED", "FAILED_ACTION")})
        if inc.diagnosis:
            d = inc.diagnosis; cc = inc.crosscheck or {}
            out.append({"ts": inc.updated_at.isoformat(), "kind": "llm", "label": "Gemini", "title": title,
                        "detail": f"Diagnosis: {d.get('category', '?')} ({d.get('transient_or_permanent', '')}), confidence {cc.get('adjusted_confidence', d.get('confidence', '?'))}, "
                                  f"proposed action {label(ACTION_LABEL, d.get('recommended_action', '?'))}, cost {usd(inc.llm_cost_usd, 3)}",
                        "inc": inc.incident_id, "ok": True})
            if cc:
                out.append({"ts": inc.updated_at.isoformat(), "kind": "det", "label": "Cross-check", "title": title,
                            "detail": "Checks: " + ", ".join(f"{c.get('check')} {'passed' if c.get('ok') else 'failed'}" for c in cc.get("checks", []))[:220],
                            "inc": inc.incident_id, "ok": bool(cc.get("passed", True))})
        for did in inc.decision_ids:
            d = dec_by_id.get(did)
            if d and d.result:
                out.append({"ts": d.created_at.isoformat(), "kind": "det", "label": "Verification", "title": title,
                            "detail": f"{label(ACTION_LABEL, d.action)}: requested {d.result.get('requested')}, observed {d.result.get('observed') or d.result.get('error')}",
                            "inc": inc.incident_id, "ok": _s(d.status) == "DONE"})
    out.sort(key=lambda e: str(e["ts"]), reverse=True)
    return out[:limit]


NAV = [("Operations", None), ("Overview", "/"), ("Fleet", "/fleet"), ("Jobs", "/jobs"), ("Incidents", "/incidents"),
       ("Control", None), ("Approvals", "/approvals"), ("Policies", "/policies"),
       ("Records", None), ("Budget", "/budget"), ("Audit Log", "/audit"), ("System Health", "/health")]

LOGO = ('<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#7cc4ff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6l7-3z"></path><path d="M9 12l2 2 4-4"></path></svg>')


def health_status(r: dict) -> tuple[str, str]:
    fails = int(r.get("consecutive_failures", 0) or 0)
    if r["src"] in PERIODIC_SOURCES:
        a = age_s(r.get("last_ok_at"))
        if a is None or a > STALE_HEALTH_S:
            return "Stale", "warn"
        return ("Healthy", "ok") if fails == 0 else ("Degraded", "warn")
    return ("Healthy", "ok") if fails == 0 else ("Failing", "crit")


def shell(title: str, path: str, data: dict | None = None):
    ui.add_head_html(CSS)
    ui.dark_mode().enable()
    frozen = data["frozen"] if data else is_frozen()
    pending = data["pending"] if data else [d for d in db.decisions.list(limit=100) if _s(d.status) == "PENDING" and _s(d.verdict) == "NEED_APPROVAL"]
    health = data["health"] if data else [d.to_dict() | {"src": d.id} for d in db.client().collection("health").stream()]
    drawer = ui.left_drawer(value=None, bordered=False).props("show-if-above width=216").classes("w-nav")
    with drawer:
        with ui.column().classes("w-full gap-0 p-2 h-full"):
            with ui.row().classes("items-center gap-2 px-2 pb-3 no-wrap"):
                ui.html(LOGO)
                with ui.column().classes("gap-0"):
                    ui.label("WARDEN").classes("font-bold text-sm tracking-wider")
                    ui.label(os.environ.get("WARDEN_PROJECT", "")[:22]).classes("text-xs w-dim")
            for name, p in NAV:
                if p is None:
                    ui.html(f'<div class="sec">{name}</div>')
                else:
                    badge = f'<span class="w-tag t-warn">{len(pending)}</span>' if (name == "Approvals" and pending) else ""
                    ui.html(f'<a href="{p}" class="{"active" if path == p else ""}"><span>{name}</span>{badge}</a>')
            with ui.column().classes("w-full gap-1 p-2").style("margin-top: auto"):
                ui.html('<div class="sec">Services</div>')
                for src in ("watcher", "steward", "deadman"):
                    h = next((x for x in health if x["src"] == src), None)
                    st, cls = health_status(h) if h else ("Unknown", "warn")
                    with ui.row().classes("items-center justify-between w-full no-wrap"):
                        ui.label(SOURCE_NAME[src]).classes("text-xs w-muted")
                        ui.label(st).classes("text-xs " + f"w-{cls}")
    with ui.header(elevated=False).classes("items-center justify-between px-3 py-2 no-wrap"):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat dense round").classes("lt-md")
            with ui.column().classes("gap-0"):
                ui.label(title).classes("text-lg font-semibold leading-tight")
                ui.label(f"{wib(_now(), '%d %b %Y, %H:%M:%S')} WIB").classes("text-xs w-muted num")
        with ui.row().classes("items-center gap-2 no-wrap"):
            if frozen:
                tag("Frozen", "crit")
                ui.button("Thaw", on_click=lambda: act("/freeze", b"freeze", on="false", who="dashboard")).props("dense no-caps unelevated color=amber-8 text-color=black").classes("px-3 font-bold")
            else:
                ui.button("Freeze", icon="pause_circle", on_click=lambda: act("/freeze", b"freeze", on="true", who="dashboard")).props("dense no-caps unelevated color=red-9").classes("px-3 font-bold")


def card_head(title: str, right: str = ""):
    with ui.element("div").classes("w-head"):
        ui.label(title)
        if right:
            ui.label(right).classes("text-xs w-muted font-normal text-right num")


def kpi(k: str, v: str, s: str = "", cls: str = "", warn: bool = False):
    with ui.element("div").classes(("w-card-warn" if warn else "w-card") + " w-kpi"):
        ui.html(f'<div class="k">{k}</div><div class="v num {cls}">{v}</div><div class="s num">{s}</div>')


def hb_status(h) -> tuple[str, str]:
    if not h:
        return "No heartbeat", "w-warn"
    a = age_s(h.ts)
    return (f"Heartbeat {rel(h.ts)}", "w-ok") if a is not None and a <= STALE_HB_S else (f"Stale — last heartbeat {rel(h.ts)}", "w-warn")


def chart_opts(hbs, contract: bool) -> dict:
    hbs = sorted(hbs, key=lambda h: h.ts)
    x = [wib(h.ts, "%H:%M") for h in hbs]
    if contract:
        series = [{"type": "line", "data": [h.step for h in hbs], "name": "Step", "lineStyle": {"color": "#7cc4ff"}, "itemStyle": {"color": "#7cc4ff"}, "symbol": "none"},
                  {"type": "line", "yAxisIndex": 1, "data": [h.loss for h in hbs], "name": "Loss", "lineStyle": {"color": "#d29922"}, "itemStyle": {"color": "#d29922"}, "symbol": "none"}]
        yaxes = [{"type": "value", "name": "Step", "axisLabel": {"color": "#5f6f83"}, "splitLine": {"lineStyle": {"color": "#161d26"}}},
                 {"type": "value", "name": "Loss", "axisLabel": {"color": "#5f6f83"}, "splitLine": {"show": False}}]
    else:
        series = [{"type": "line", "data": [h.cpu_pct for h in hbs], "name": "CPU %", "lineStyle": {"color": "#7cc4ff"}, "itemStyle": {"color": "#7cc4ff"}, "symbol": "none"},
                  {"type": "line", "data": [h.gpu_util for h in hbs], "name": "GPU %", "lineStyle": {"color": "#3fb950"}, "itemStyle": {"color": "#3fb950"}, "symbol": "none"}]
        yaxes = [{"type": "value", "max": 100, "axisLabel": {"color": "#5f6f83"}, "splitLine": {"lineStyle": {"color": "#161d26"}}}]
    return {"backgroundColor": "transparent", "grid": {"left": 52, "right": 52, "top": 30, "bottom": 26}, "legend": {"textStyle": {"color": "#8797ab"}, "top": 0},
            "xAxis": {"type": "category", "data": x, "axisLabel": {"color": "#5f6f83"}}, "yAxis": yaxes, "series": series}


def heartbeat_chart(job_id: str):
    hbs = db.recent_heartbeats(job_id, 60) if job_id else []
    if not hbs:
        return
    contract = any((not h.synthetic) and h.loss is not None for h in hbs)
    with ui.element("div").classes("px-3 pb-3"):
        ui.label("Training Heartbeat" if contract else "Host Heartbeat").classes("w-lbl pb-1")
        ui.echart(chart_opts(hbs, contract)).classes("w-full").style("height: 170px")


def approval_card(dec, inc, compact: bool = False):
    exp = dec.expires_at
    expired = bool(exp and exp < _now())
    did = dec.decision_id
    with ui.element("div").classes("w-card-warn p-3 flex flex-col gap-3 w-full"):
        with ui.row().classes("items-center justify-between w-full no-wrap"):
            tag("Approval Required", "warn")
            ui.label(("Expired " + rel(exp)) if expired else ("Expires " + rel(exp)) if exp else "").classes("text-xs num " + ("w-crit" if expired else "w-warn"))
        ui.link(f"{label(ACTION_LABEL, dec.action)} — {dec.job_id or dec.params.get('instance_ref') or '—'}", f"/incidents/{dec.incident_id}").classes("font-semibold text-base w-link")
        rows: list[tuple[str, Any]] = [("INSTANCE", dec.params.get("instance_ref") or "—"), ("AUTONOMY", _s(dec.autonomy)),
                                       ("BLAST RADIUS", label(RADIUS_LABEL, dec.blast_radius)), ("ESTIMATED COST", usd(dec.cost_usd, 3))]
        if inc and inc.diagnosis:
            d = inc.diagnosis; cc = inc.crosscheck or {}
            rows.insert(0, ("DIAGNOSIS", lambda: (tag("Gemini", "llm"), ui.label(f" {d.get('category')} · confidence {cc.get('adjusted_confidence', d.get('confidence'))} · {usd(inc.llm_cost_usd, 3)}").classes("text-xs"))))
        elif inc:
            rows.insert(0, ("TRIGGER", lambda: (tag("Rule", "det"), ui.label(f" {label(RULE_LABEL, inc.rule)}").classes("text-xs"))))
        kv(rows)
        if dec.explain and not compact:
            ui.html('<div class="w-lbl">Policy Evaluation</div><div class="w-code">' + "<br>".join(e for e in dec.explain[:6]) + "</div>")
        if inc and (inc.diagnosis or {}).get("evidence_quotes") and not compact:
            ui.html('<div class="w-lbl">Evidence</div><div class="w-code w-log mono">' + "<br>".join(str(q)[:140] for q in inc.diagnosis["evidence_quotes"][:4]) + "</div>")
        plan = dec.dry_run_plan.get("plan") if dec.dry_run_plan else None
        if plan:
            ui.html('<div class="w-lbl">Execution Plan</div>')
            kv(plan_rows(plan))
        if expired:
            ui.button("Re-evaluate", on_click=lambda _, i=did: act(f"/decisions/{i}/reevaluate", i.encode(), who="dashboard")).props("no-caps unelevated color=blue-grey-9").classes("w-full").style("height:44px")
        else:
            with ui.element("div").classes("grid grid-cols-3 gap-2"):
                ui.button("Approve", on_click=lambda _, i=did: act(f"/decisions/{i}/approve", i.encode(), who="dashboard")).props("no-caps unelevated color=green-8").classes("font-bold").style("height:44px")
                ui.button("Deny", on_click=lambda _, i=did: act(f"/decisions/{i}/deny", i.encode(), who="dashboard")).props("no-caps unelevated color=blue-grey-9").style("height:44px")
                ui.button("Always (24 h)", on_click=lambda _, i=did: act(f"/decisions/{i}/always", i.encode(), who="dashboard")).props("no-caps unelevated color=blue-grey-9").style("height:44px")


def incident_row(inc):
    with ui.element("div").classes("w-row px-3 py-2 grid gap-2 items-center cursor-pointer").style("grid-template-columns: 76px minmax(0,1fr) auto").on("click", lambda _, i=inc.incident_id: ui.navigate.to(f"/incidents/{i}")):
        tag(inc.severity, sev_kind(inc.severity))
        with ui.column().classes("gap-0 min-w-0"):
            ui.label(f"{label(RULE_LABEL, inc.rule)} — {inc.job_id or inc.instance_ref}").classes("font-semibold text-sm truncate w-full")
            ui.label(f"{wib(inc.created_at, '%d %b %H:%M')} WIB ({rel(inc.created_at)})").classes("text-xs w-muted truncate w-full num")
        tag(label(STATE_LABEL, inc.state), state_kind(_s(inc.state)))


def job_card(j, h, e: dict | None = None, inst=None):
    txt, cls = hb_status(h)
    with ui.element("div").classes("w-row px-3 py-2 flex flex-col gap-2"):
        with ui.row().classes("items-center justify-between w-full no-wrap"):
            ui.link(j.job_id, "/jobs").classes("font-semibold w-link truncate")
            tag(label(JOB_STATUS_LABEL, j.status) + (f" · {j.phase}" if j.phase else ""), "ok" if _s(j.status) == "RUNNING" else "grey" if _s(j.status) == "COMPLETE" else "warn")
        rows: list[tuple[str, Any]] = []
        if h and h.step is not None and not h.synthetic:
            rows.append(("PROGRESS", f"step {h.step:,}" + (f", loss {h.loss:.4f}" if h.loss is not None else "")))
        elif h:
            rows.append(("HOST", f"CPU {h.cpu_pct or 0:.0f} %, GPU {f'{h.gpu_util:.0f} %' if h.gpu_util is not None else '—'}, disk {h.disk_avail_gb or 0:.1f} GB free"))
        rows.append(("HEARTBEAT", lambda: ui.label(txt).classes(cls)))
        rows.append(("MODE", "Legacy (log parser)" if j.legacy else "Instrumented"))
        if e and e.get("ettr") is not None:
            rows.append(("ETTR (7 D)", f"{e['ettr']:.2f}  ({e.get('effective_h')} h effective / {e.get('paid_h')} h paid)"))
        if j.last_good_ckpt.get("path"):
            rows.append(("LAST VERIFIED CKPT", j.last_good_ckpt["path"]))
        if inst:
            rows.append(("INSTANCE", f"{inst.name} · {_s(inst.status)} · {'Spot' if inst.spot else 'On-demand'} · {usd(inst.hourly_price_usd, 3)}/h"))
        kv(rows)


def health_grid(rows: list[dict], cols: int = 2):
    with ui.element("div").classes("grid").style(f"grid-template-columns: repeat({cols}, minmax(0,1fr))"):
        for r in sorted(rows, key=lambda x: SOURCE_NAME.get(x["src"], x["src"])):
            st, cls = health_status(r)
            with ui.element("div").classes("w-row px-3 py-2 flex items-center justify-between gap-2 no-wrap"):
                ui.label(SOURCE_NAME.get(r["src"], r["src"])).classes("text-sm truncate min-w-0")
                ui.label(st).classes("text-xs " + f"w-{cls}")


def cost_chart(proj: dict):
    days = sorted((d.to_dict() | {"day": d.id} for d in db.client().collection("costs").stream()), key=lambda x: x["day"])
    xs = [d["day"][5:] for d in days]
    cum, run = [], 0.0
    for d in days:
        run += float(d.get("compute_usd", 0.0)) + float(d.get("llm_usd", 0.0)); cum.append(round(run, 4))
    ui.echart({"backgroundColor": "transparent", "grid": {"left": 52, "right": 16, "top": 30, "bottom": 28},
               "xAxis": {"type": "category", "data": xs, "axisLabel": {"color": "#5f6f83"}},
               "yAxis": {"type": "value", "axisLabel": {"color": "#5f6f83", "formatter": "${value}"}, "splitLine": {"lineStyle": {"color": "#161d26"}}},
               "series": [{"type": "line", "name": "Cumulative spend", "data": cum, "lineStyle": {"color": "#7cc4ff", "width": 2}, "itemStyle": {"color": "#7cc4ff"}, "areaStyle": {"color": "rgba(124,196,255,0.08)"}},
                          {"type": "line", "name": "Budget cap", "data": [proj["cap_usd"]] * len(xs), "lineStyle": {"color": "#3a4656", "type": "dashed"}, "itemStyle": {"color": "#3a4656"}, "symbol": "none"}],
               "legend": {"textStyle": {"color": "#8797ab"}, "right": 8, "top": 0}}).classes("w-full").style("height: 170px")


def auto_reload():
    ui.timer(RELOAD_S, ui.navigate.reload, once=True)


def empty(text: str):
    ui.label(text).classes("w-muted p-4 text-sm")


@ui.page("/")
def overview():
    data = load_all()
    shell("Overview", "/", data)
    jobs, insts, incs, pending, hb, proj = data["jobs"], data["insts"], data["incs"], data["pending"], data["hb"], data["proj"]
    running = [i for i in insts if _s(i.status) == "RUNNING"]
    open_incs = [i for i in incs if _s(i.state) not in ("RESOLVED", "CLOSED", "FALSE_POSITIVE")]
    day_start = _now().astimezone(WIB).replace(hour=0, minute=0, second=0, microsecond=0)
    resolved_today = [i for i in incs if _s(i.state) in ("RESOLVED", "CLOSED") and i.updated_at >= day_start]
    from warden.steward import ledger
    ettrs = {j.job_id: ledger.ettr(j.job_id, 168) for j in jobs}
    eff = sum(e.get("effective_h") or 0 for e in ettrs.values()); paid = sum(e.get("paid_h") or 0 for e in ettrs.values())
    soonest = min((d.expires_at for d in pending if d.expires_at), default=None)
    inst_by_job = {i.job_id: i for i in insts if i.job_id}
    with ui.column().classes("w-full gap-4 p-4"):
        with ui.element("div").classes("grid gap-3 w-full w-kpis").style("grid-template-columns: repeat(auto-fit, minmax(165px, 1fr))"):
            kpi("Pending Approvals", str(len(pending)), ("Soonest expires " + rel(soonest)) if soonest else "", "w-warn" if pending else "", warn=bool(pending))
            kpi("Open Incidents", str(len(open_incs)), f"{len(resolved_today)} resolved today", "w-warn" if open_incs else "")
            kpi("Jobs", str(len(jobs)), f"{sum(1 for j in jobs if _s(j.status) == 'RUNNING')} running, {sum(1 for j in jobs if _s(j.status) == 'COMPLETE')} complete")
            kpi("Instances Running", f"{len(running)}<span style='font-size:14px;color:var(--dim)'> / {len(insts)}</span>", f"Burn rate {usd(proj['burn_usd_per_hour'], 3)}/h")
            kpi("Fleet ETTR (7 d)", f"{(eff / paid):.2f}" if paid else "—", f"{eff:.2f} h effective / {paid:.2f} h paid" if paid else "")
            kpi("Spend Today", usd(proj["today_usd"]), f"Month to date {usd(proj['month_to_date_usd'])} of {usd(proj['cap_usd'], 0)} cap")
        with ui.element("div").classes("grid gap-3 w-full items-start").style("grid-template-columns: repeat(auto-fit, minmax(340px, 1fr))"):
            with ui.element("div").classes("w-card"):
                with ui.element("div").classes("w-head"):
                    ui.label("Activity")
                    ui.html('<span class="text-xs font-normal"><span class="w-tag t-det">Rule</span>&nbsp;<span class="w-tag t-llm">Gemini</span></span>')
                evs = activity(incs, data["decs"], 14)
                if not evs:
                    empty("No activity recorded.")
                for e in evs:
                    with ui.element("div").classes("w-row w-act px-3 py-2 grid gap-2 items-start cursor-pointer").on("click", lambda _, i=e["inc"]: ui.navigate.to(f"/incidents/{i}")):
                        ui.html(f'<div class="num text-xs w-muted">{wib(e["ts"])}<br><span class="w-dim">{rel(e["ts"])}</span></div>')
                        tag(e["label"], e["kind"])
                        with ui.column().classes("gap-0 min-w-0"):
                            ui.label(e["title"]).classes("text-sm font-semibold w-full")
                            ui.label(e["detail"]).classes("text-xs w-muted w-full")
                        ui.html('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="%s" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle>%s</svg>'
                                % (("#3fb950", '<path d="M8 12l3 3 5-6"></path>') if e["ok"] else ("#f85149", '<path d="M9 9l6 6M15 9l-6 6"></path>')))
            with ui.column().classes("gap-3 w-order-first w-full"):
                if pending:
                    for d in pending[:2]:
                        approval_card(d, db.incidents.get(d.incident_id) if d.incident_id else None, compact=True)
                    if len(pending) > 2:
                        ui.link(f"View all {len(pending)} pending approvals", "/approvals").classes("text-sm")
                else:
                    with ui.element("div").classes("w-card w-full"):
                        card_head("Pending Approvals", "0")
                        empty("No pending approvals.")
                with ui.element("div").classes("w-card w-full"):
                    card_head("Recent Incidents", str(len(incs)))
                    if not incs:
                        empty("No incidents recorded.")
                    for inc in incs[:6]:
                        incident_row(inc)
            with ui.column().classes("gap-3 w-full"):
                with ui.element("div").classes("w-card w-full"):
                    card_head("Jobs", str(len(jobs)))
                    if not jobs:
                        empty("No jobs registered.")
                    for j in sorted(jobs, key=lambda x: (_s(x.status) != "RUNNING", x.job_id)):
                        job_card(j, hb.get(j.job_id), ettrs.get(j.job_id), inst_by_job.get(j.job_id))
                with ui.element("div").classes("w-card w-full"):
                    card_head("Recent Actions")
                    rows = [d.to_dict() for d in db.client().collection("audit").order_by("ts", direction="DESCENDING").limit(40).stream()]
                    res = [r for r in rows if r.get("phase") == "result"][:5]
                    if not res:
                        empty("No actions recorded.")
                    for r in res:
                        ok = r.get("ok")
                        with ui.element("div").classes("w-row px-3 py-2 grid gap-2 items-center text-xs num").style("grid-template-columns: 44px minmax(0,1fr) minmax(0,1fr) 60px"):
                            ui.label(wib(r.get("ts"), "%H:%M")).classes("w-muted")
                            ui.label(f"{label(ACTION_LABEL, r.get('action'))} — {str(r.get('target', '')).split('/')[-1]}").classes("truncate")
                            ui.label((str((r.get("after") or {}).get("observed") or "Done")[:40]) if ok else ("Failed: " + str(r.get("error", ""))[:40])).classes("truncate " + ("w-ok" if ok else "w-crit"))
                            ui.label("Operator" if str(r.get("actor", "")).startswith("human") else "Warden").classes("w-muted truncate")
                with ui.element("div").classes("w-card w-full"):
                    card_head("System Health")
                    health_grid(data["health"])
        with ui.element("div").classes("w-card p-3 grid gap-3 w-full").style("grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))"):
            with ui.column().classes("gap-2"):
                ui.label("Budget").classes("font-semibold text-sm")
                kv([("MONTH TO DATE", usd(proj["month_to_date_usd"])), ("BUDGET CAP", usd(proj["cap_usd"], 0)), ("BURN RATE", f"{usd(proj['burn_usd_per_hour'], 3)}/h"),
                    ("RUNWAY", f"{proj['runway_days']} days" if proj["runway_days"] else "—"), ("30-DAY PROJECTION", usd(proj["if_left_running_30d_usd"]))])
                ui.button("Run Steward Sweep", on_click=lambda: (notify_result(core("/steward")), ui.timer(0.9, ui.navigate.reload, once=True))).props("no-caps dense unelevated color=blue-grey-9")
            cost_chart(proj)
    auto_reload()


@ui.page("/fleet")
def fleet():
    data = load_all()
    shell("Fleet", "/fleet", data)
    insts = sorted(data["insts"], key=lambda x: x.ref)
    with ui.column().classes("w-full gap-3 p-4"):
        if not insts:
            with ui.element("div").classes("w-card w-full"):
                empty("No managed instances.")
        for i in insts:
            h = data["hb"].get(i.job_id) if i.job_id else None
            txt, cls = hb_status(h)
            with ui.element("div").classes("w-card p-3 flex flex-col gap-2 w-full"):
                with ui.row().classes("items-center justify-between w-full no-wrap"):
                    ui.label(i.name).classes("font-semibold")
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        if i.termination_action == "DELETE" or i.boot_disk_auto_delete:
                            tag("Unsafe configuration", "crit")
                        tag(_s(i.status), "ok" if _s(i.status) == "RUNNING" else "grey")
                rows: list[tuple[str, Any]] = [("ZONE", i.zone), ("MACHINE TYPE", i.machine_type or "—"), ("PROVISIONING", "Spot" if i.spot else "On-demand"),
                                               ("PRICE", f"{usd(i.hourly_price_usd, 3)}/h"), ("JOB", i.job_id or "—"), ("ON TERMINATION", i.termination_action or "—"),
                                               ("HEARTBEAT", lambda: ui.label(txt).classes(cls))]
                if h:
                    rows.append(("HOST", f"phase {h.phase or '—'}, step {h.step if h.step is not None else '—'}, CPU {h.cpu_pct or 0:.0f} %, GPU {f'{h.gpu_util:.0f} %' if h.gpu_util is not None else '—'}, disk {h.disk_avail_gb or 0:.1f} GB free"))
                rows.append(("LAST SEEN", when(i.last_seen)))
                if i.operator_active_until and i.operator_active_until > _now():
                    rows.append(("OPERATOR SESSION", f"until {wib(i.operator_active_until, '%H:%M')} WIB"))
                kv(rows)
    auto_reload()


@ui.page("/jobs")
def jobs_page():
    data = load_all()
    shell("Jobs", "/jobs", data)
    from warden.steward import ledger
    inst_by_job = {i.job_id: i for i in data["insts"] if i.job_id}
    with ui.column().classes("w-full gap-3 p-4"):
        if not data["jobs"]:
            with ui.element("div").classes("w-card w-full"):
                empty("No jobs registered.")
        for j in sorted(data["jobs"], key=lambda x: (_s(x.status) != "RUNNING", x.job_id)):
            with ui.element("div").classes("w-card w-full"):
                job_card(j, data["hb"].get(j.job_id), ledger.ettr(j.job_id, 168), inst_by_job.get(j.job_id))
                heartbeat_chart(j.job_id)
                with ui.element("div").classes("px-3 pb-3"):
                    kv([("RUN ID", j.run_id or "—"), ("SPENT", usd(j.spent_usd, 3)), ("EXPECTATIONS", json.dumps(j.expect)[:160] if j.expect else "—")]
                       + ([("OPERATOR HOLD", f"until {wib(j.operator_hold_until, '%H:%M')} WIB")] if j.operator_hold_until and j.operator_hold_until > _now() else []))
    auto_reload()


@ui.page("/incidents")
def incidents():
    data = load_all()
    shell("Incidents", "/incidents", data)
    incs = data["incs"]
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("w-card w-full"):
            card_head("All Incidents", str(len(incs)))
            if not incs:
                empty("No incidents recorded.")
            for inc in incs[:150]:
                incident_row(inc)
    auto_reload()


@ui.page("/incidents/{incident_id}")
def incident_detail(incident_id: str):
    inc = db.incidents.get(incident_id)
    shell("Incident", "/incidents")
    if not inc:
        empty("Incident not found."); return
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("w-card p-3 flex flex-col gap-2 w-full"):
            with ui.row().classes("items-center gap-2"):
                tag(label(STATE_LABEL, inc.state), state_kind(_s(inc.state))); tag(inc.severity, sev_kind(inc.severity))
            ui.label(f"{label(RULE_LABEL, inc.rule)} — {inc.job_id or inc.instance_ref}").classes("font-semibold text-base")
            ui.label(inc.summary).classes("text-sm w-muted")
            kv([("INCIDENT ID", inc.incident_id), ("RULE", inc.rule), ("JOB", inc.job_id or "—"), ("INSTANCE", inc.instance_ref or "—"),
                ("OPENED", when(inc.created_at)), ("UPDATED", when(inc.updated_at)), ("BURN RATE", f"{usd(inc.cost_burning_usd_per_hour, 3)}/h"), ("LLM COST", usd(inc.llm_cost_usd, 3))])
        for did in inc.decision_ids:
            dec = db.decisions.get(did)
            if dec and _s(dec.status) == "PENDING" and _s(dec.verdict) == "NEED_APPROVAL":
                approval_card(dec, inc)
        if inc.diagnosis:
            d = inc.diagnosis; cc = inc.crosscheck or {}
            with ui.element("div").classes("w-card w-full"):
                with ui.element("div").classes("w-head"):
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        tag("Gemini", "llm"); ui.label("Diagnosis")
                    tag("Cross-check passed" if cc.get("passed") else "Cross-check failed", "ok" if cc.get("passed") else "crit")
                with ui.column().classes("p-3 gap-2"):
                    if d.get("human_summary") or d.get("human_summary_id"):
                        ui.label(d.get("human_summary") or d.get("human_summary_id")).classes("text-sm")
                    kv([("CATEGORY", d.get("category", "—")), ("NATURE", d.get("transient_or_permanent", "—")), ("CONFIDENCE", str(cc.get("adjusted_confidence", d.get("confidence", "—")))),
                        ("PROPOSED ACTION", label(ACTION_LABEL, d.get("recommended_action", "—"))), ("NEEDS HUMAN", "Yes" if d.get("needs_human") else "No"),
                        ("MODEL", d.get("model") or "—"), ("COST", usd(inc.llm_cost_usd, 3))])
                    if cc.get("checks"):
                        ui.html('<div class="w-lbl pt-1">Cross-check</div>')
                        for c in cc.get("checks", []):
                            ui.label(f"{'Passed' if c.get('ok') else 'Failed'} — {c.get('check')}" + (f": {c.get('note')}" if c.get("note") else "")).classes("text-xs " + ("w-ok" if c.get("ok") else "w-crit"))
                    if d.get("evidence_quotes"):
                        ui.html('<div class="w-lbl pt-1">Evidence</div><div class="w-code w-log mono">' + "<br>".join(str(q)[:200] for q in d["evidence_quotes"]) + "</div>")
                    if d.get("falsifiable_check"):
                        kv([("FALSIFIABLE CHECK", d["falsifiable_check"])])
        for eid in inc.evidence_ids:
            ev = db.evidence.get(eid)
            if not ev:
                continue
            with ui.element("div").classes("w-card w-full"):
                card_head({"rule": "Rule Evidence", "log_window": "Log Excerpt", "artifact_check": "Artifact Verification", "heartbeat": "Heartbeat Evidence"}.get(ev.kind, f"Evidence — {ev.kind}"), when(ev.created_at))
                with ui.column().classes("p-3 gap-1"):
                    if ev.kind == "artifact_check":
                        for r in ev.payload.get("results", []):
                            ui.label(f"{'Passed' if r.get('ok') else 'Failed'} — {r.get('name')} · {r.get('bytes', 0):,} B" + (f" · {r.get('reason')}" if r.get("reason") else "")).classes("text-xs mono " + ("w-ok" if r.get("ok") else "w-crit"))
                    else:
                        ui.label(ev.summary).classes("text-sm")
                        if ev.payload:
                            kv([(k.upper().replace("_", " "), json.dumps(v, ensure_ascii=False)[:300] if isinstance(v, (dict, list)) else _s(v)) for k, v in list(ev.payload.items())[:8]])
        hbs = db.recent_heartbeats(inc.job_id, 60) if inc.job_id else []
        if hbs:
            contract = any((not h.synthetic) and h.loss is not None for h in hbs)
            with ui.element("div").classes("w-card w-full"):
                card_head("Training Heartbeat" if contract else "Host Heartbeat", "last 60")
                ui.echart(chart_opts(hbs, contract)).classes("w-full").style("height: 180px")
        for did in inc.decision_ids:
            dec = db.decisions.get(did)
            if not dec or (_s(dec.status) == "PENDING" and _s(dec.verdict) == "NEED_APPROVAL"):
                continue
            with ui.element("div").classes("w-card w-full"):
                with ui.element("div").classes("w-head"):
                    ui.label(f"Decision — {label(ACTION_LABEL, dec.action)}")
                    tag(label(DEC_LABEL, dec.status), "ok" if _s(dec.status) == "DONE" else "crit" if _s(dec.status) in ("FAILED", "EXPIRED") else "grey")
                with ui.column().classes("p-3 gap-2"):
                    kv([("VERDICT", label(VERDICT_LABEL, dec.verdict)), ("AUTONOMY", _s(dec.autonomy)), ("BLAST RADIUS", label(RADIUS_LABEL, dec.blast_radius)), ("ESTIMATED COST", usd(dec.cost_usd, 3)),
                        ("CREATED", when(dec.created_at))] + ([("EXPIRES", when(dec.expires_at))] if dec.expires_at else []) + ([("APPROVED BY", dec.approved_by)] if dec.approved_by else []))
                    if dec.explain:
                        ui.html('<div class="w-lbl">Policy Evaluation</div><div class="w-code">' + "<br>".join(dec.explain) + "</div>")
                    if dec.dry_run_plan.get("plan"):
                        ui.html('<div class="w-lbl">Execution Plan</div>'); kv(plan_rows(dec.dry_run_plan["plan"]))
                    if dec.result:
                        kv([("REQUESTED", str(dec.result.get("requested", "—"))), ("OBSERVED", lambda: ui.label(str(dec.result.get("observed") or dec.result.get("error") or "—")).classes("w-ok" if _s(dec.status) == "DONE" else "w-crit"))])
                    if _s(dec.status) in ("EXPIRED", "REJECTED", "FAILED"):
                        ui.button("Re-evaluate", on_click=lambda _, i=dec.decision_id: act(f"/decisions/{i}/reevaluate", i.encode(), who="dashboard")).props("no-caps dense unelevated color=blue-grey-9")
        with ui.element("div").classes("w-card w-full"):
            card_head("Timeline", f"{len(inc.timeline)} steps")
            with ui.element("div").classes("w-tl grid gap-2 px-3 py-1 w-th"):
                ui.label("Time"); ui.label("Transition"); ui.label("Note"); ui.label("Actor")
            for t in inc.timeline:
                with ui.element("div").classes("w-row w-tl px-3 py-2 grid gap-2 items-start text-xs"):
                    ui.html(f'<div class="num w-muted">{wib(t.get("ts"))}<br><span class="w-dim">{rel(t.get("ts"))}</span></div>')
                    ui.label(f"{label(STATE_LABEL, t.get('from', ''))} → {label(STATE_LABEL, t.get('to', ''))}").classes("num")
                    ui.label(t.get("note", "")).classes("w-muted")
                    ui.label("Operator" if str(t.get("actor", "")).startswith("human") else "Warden").classes("w-dim truncate")


@ui.page("/approvals")
def approvals_page():
    data = load_all()
    shell("Approvals", "/approvals", data)
    decs, pending = data["decs"], data["pending"]
    stale = [d for d in decs if _s(d.status) in ("EXPIRED", "REJECTED", "FAILED")][:10]
    with ui.column().classes("w-full gap-3 p-4"):
        if not pending:
            with ui.element("div").classes("w-card w-full"):
                card_head("Pending Approvals", "0")
                empty("No pending approvals.")
        for d in pending:
            approval_card(d, db.incidents.get(d.incident_id) if d.incident_id else None)
        if stale:
            with ui.element("div").classes("w-card w-full"):
                card_head("Expired, Rejected and Failed Decisions", str(len(stale)))
                for d in stale:
                    with ui.element("div").classes("w-row px-3 py-2 grid gap-2 items-center").style("grid-template-columns: minmax(0,1fr) auto"):
                        with ui.column().classes("gap-0 min-w-0"):
                            ui.link(f"{label(ACTION_LABEL, d.action)} — {d.job_id or '—'}", f"/incidents/{d.incident_id}").classes("text-sm font-semibold w-link")
                            ui.label(f"{label(DEC_LABEL, d.status)} · {_s(d.autonomy)} · {when(d.created_at)}").classes("text-xs w-muted truncate w-full num")
                        ui.button("Re-evaluate", on_click=lambda _, i=d.decision_id: act(f"/decisions/{i}/reevaluate", i.encode(), who="dashboard")).props("no-caps dense unelevated color=blue-grey-9")
        ov = [x.to_dict() | {"id": x.id} for x in db.client().collection("policy_overrides").stream()]
        with ui.element("div").classes("w-card w-full"):
            card_head("Active Overrides", str(len(ov)))
            if not ov:
                empty("No active overrides.")
            for o in ov:
                until = datetime.fromtimestamp(float(o.get("until", 0)), tz=timezone.utc)
                job, action = (o["id"].split(":", 1) + [""])[:2]
                with ui.element("div").classes("w-row px-3 py-2"):
                    kv([("JOB", job), ("ACTION", label(ACTION_LABEL, action)), ("LEVEL", o.get("level", "—")), ("GRANTED BY", o.get("by", "—")), ("UNTIL", when(until))])
    auto_reload()


@ui.page("/policies")
def policies():
    from warden.policy.engine import load_policy
    data = load_all()
    shell("Policies", "/policies", data)
    pol = load_policy()
    level_text = {"L0": "L0 — Observe", "L1": "L1 — Propose, require approval", "L2": "L2 — Act, then report", "L3": "L3 — Act silently"}
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("w-card w-full"):
            card_head("Autonomy Levels by Action")
            with ui.element("div").classes("grid px-3 py-1 w-th").style("grid-template-columns: 200px 220px minmax(0,1fr)"):
                ui.label("Action"); ui.label("Level"); ui.label("Limits")
            for a, l in pol["autonomy"].items():
                with ui.element("div").classes("w-row grid px-3 py-2 text-sm items-center").style("grid-template-columns: 200px 220px minmax(0,1fr)"):
                    ui.label(label(ACTION_LABEL, a))
                    ui.label(level_text.get(str(l), str(l))).classes("text-xs " + ("w-ok" if str(l) in ("L2", "L3") else "w-warn" if str(l) == "L1" else "w-muted"))
                    lim = pol["limits"].get(a, {})
                    ui.label(", ".join(f"{k.replace('_', ' ')} {v}" for k, v in lim.items()) if lim else "—").classes("text-xs w-muted num")
        with ui.element("div").classes("grid gap-3 w-full").style("grid-template-columns: repeat(auto-fit, minmax(300px, 1fr))"):
            for title, obj in (("Global Caps", pol["global"]), ("Circuit Breaker", pol["circuit_breaker"])):
                with ui.element("div").classes("w-card"):
                    card_head(title)
                    with ui.element("div").classes("p-3"):
                        kv([(k.replace("_", " ").upper(), _s(v)) for k, v in obj.items()])
            with ui.element("div").classes("w-card"):
                card_head("Hard Deny")
                with ui.element("div").classes("p-3 flex flex-col gap-1"):
                    for x in pol["hard_deny"]:
                        ui.label(label(ACTION_LABEL, x)).classes("text-sm w-crit")


@ui.page("/budget")
def budget():
    from warden.steward import ledger
    data = load_all()
    shell("Budget", "/budget", data)
    p = data["proj"]
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("grid gap-3 w-full w-kpis").style("grid-template-columns: repeat(auto-fit, minmax(160px, 1fr))"):
            kpi("Spend Today", usd(p["today_usd"]))
            kpi("Month to Date", usd(p["month_to_date_usd"]), f"{100 * p['month_to_date_usd'] / max(p['cap_usd'], 1):.1f} % of {usd(p['cap_usd'], 0)}")
            kpi("Burn Rate", f"{usd(p['burn_usd_per_hour'], 3)}/h")
            kpi("Runway", f"{p['runway_days']} d" if p["runway_days"] else "—")
            kpi("30-Day Projection", usd(p["if_left_running_30d_usd"]))
        with ui.element("div").classes("w-card w-full"):
            card_head("Cumulative Spend vs Budget Cap")
            with ui.element("div").classes("p-3"):
                cost_chart(p)
        with ui.element("div").classes("w-card w-full"):
            card_head("ETTR by Job (7 d)")
            with ui.element("div").classes("grid px-3 py-1 w-th").style("grid-template-columns: minmax(0,1fr) 80px 120px 120px 100px"):
                ui.label("Job"); ui.label("ETTR"); ui.label("Effective"); ui.label("Paid"); ui.label("Spent")
            for j in data["jobs"]:
                e = ledger.ettr(j.job_id, 168)
                with ui.element("div").classes("w-row px-3 py-2 grid gap-2 items-center text-sm num").style("grid-template-columns: minmax(0,1fr) 80px 120px 120px 100px"):
                    ui.label(j.job_id).classes("truncate")
                    ui.label(f"{e['ettr']:.2f}" if e.get("ettr") is not None else "—").classes("font-semibold")
                    ui.label(f"{e.get('effective_h', '—')} h" if e.get("ettr") is not None else "—").classes("w-muted")
                    ui.label(f"{e.get('paid_h', '—')} h" if e.get("ettr") is not None else "—").classes("w-muted")
                    ui.label(usd(j.spent_usd, 3)).classes("w-muted")
        ui.button("Run Steward Sweep", on_click=lambda: (notify_result(core("/steward")), ui.timer(0.9, ui.navigate.reload, once=True))).props("no-caps unelevated color=blue-grey-9")
    auto_reload()


@ui.page("/audit")
def audit():
    data = load_all()
    shell("Audit Log", "/audit", data)
    rows = [d.to_dict() for d in db.client().collection("audit").order_by("ts", direction="DESCENDING").limit(300).stream()]
    cols = "grid-template-columns: 150px 80px 70px 170px minmax(0,1fr) minmax(0,1fr)"
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("w-card w-full").style("overflow-x: auto"):
            card_head("Audit Log", f"{len(rows)} entries")
            with ui.element("div").classes("grid px-3 py-1 w-th").style(cols + "; min-width: 760px"):
                for h in ("Time", "Actor", "Phase", "Action", "Target", "Result"):
                    ui.label(h)
            if not rows:
                empty("No entries.")
            for r in rows:
                ok = r.get("ok")
                with ui.element("div").classes("w-row grid px-3 py-2 text-xs items-center num").style(cols + "; min-width: 760px"):
                    ui.label(wib(r.get("ts"), "%d %b %H:%M:%S")).classes("w-muted")
                    ui.label("Operator" if str(r.get("actor", "")).startswith("human") else str(r.get("actor", "")).capitalize())
                    ui.label(str(r.get("phase", "")).capitalize())
                    ui.label(label(ACTION_LABEL, r.get("action", ""))).classes("truncate")
                    ui.label(str(r.get("target", ""))).classes("truncate w-muted")
                    ui.label("—" if ok is None else (str((r.get("after") or {}).get("observed") or "Done")[:60] if ok else "Failed: " + str(r.get("error", ""))[:60])).classes("truncate " + ("" if ok is None else "w-ok" if ok else "w-crit"))


@ui.page("/health")
def health():
    data = load_all()
    shell("System Health", "/health", data)
    rows = data["health"]
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("w-card w-full"):
            card_head("Components", str(len(rows)))
            if not rows:
                empty("No health records.")
            for r in sorted(rows, key=lambda x: SOURCE_NAME.get(x["src"], x["src"])):
                st, cls = health_status(r)
                with ui.element("div").classes("w-row px-3 py-2 flex flex-col gap-2"):
                    with ui.row().classes("items-center justify-between w-full no-wrap"):
                        ui.label(SOURCE_NAME.get(r["src"], r["src"])).classes("font-semibold text-sm")
                        tag(st, cls)
                    kv([("LAST OK", when(r.get("last_ok_at"))), ("CONSECUTIVE FAILURES", str(r.get("consecutive_failures", 0)))]
                       + ([("LAST ERROR", str(r.get("last_error", ""))[:160])] if r.get("last_error") else [])
                       + ([("STATS", json.dumps(r["stats"]))] if r.get("stats") else []))
    auto_reload()


def run():
    ui.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), title="Warden", reload=False, show=False, dark=True,
           storage_secret=os.environ.get("WARDEN_UI_SECRET", "dev"))


if __name__ in {"__main__", "__mp_main__"}:
    run()
