"""warden-ui (Fase 8): NiceGUI dashboard. Data: Firestore (reload every 30 s). Actions go through warden-core (HMAC).
Pages: / (overview) · /fleet · /jobs · /incidents · /incidents/{id} · /approvals · /policies · /budget · /audit · /health.
Language: English. Times: WIB (UTC+7) + relative age. Every number names its source; stale data is marked STALE."""
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
.w-head { display:flex; align-items:center; justify-content:space-between; gap: 8px; padding: 10px 14px; border-bottom: 1px solid var(--line); font-weight: 600; }
.w-row { border-bottom: 1px solid var(--line2); }
.w-row:last-child { border-bottom: none; }
.w-muted { color: var(--muted); } .w-dim { color: var(--dim); } .w-ok { color: var(--ok); } .w-warn { color: var(--warn); } .w-crit { color: var(--crit); }
.w-tag { display:inline-flex; align-items:center; padding: 2px 7px; border-radius: 4px; font-size: 10px; font-weight: 700; letter-spacing: .06em; white-space: nowrap; line-height: 1.5; }
.t-det { background: var(--det); color: #cfdbe8; } .t-llm { background: var(--llm); color: #f1eaff; } .t-ok { background:#0f1f17; color: var(--ok); }
.t-warn { background:#2a220e; color: var(--warn); } .t-crit { background:#3d1d1f; color: var(--crit); } .t-grey { background:#17212d; color:#aab6c4; }
.w-kpi { padding: 12px 14px; display:flex; flex-direction:column; gap: 3px; min-width: 0; }
.w-kpi .k { font-size: 11px; color: var(--muted); letter-spacing: .06em; } .w-kpi .v { font-size: 26px; font-weight: 600; line-height: 1.1; } .w-kpi .s { font-size: 11px; color: var(--dim); }
.w-nav a { display:flex; align-items:center; justify-content:space-between; padding: 8px 10px; border-radius: 6px; color:#aab6c4; text-decoration:none; font-size: 13px; }
.w-nav a.active { background:#17212d; color: var(--text); font-weight: 600; }
.w-nav .sec { font-size: 10px; letter-spacing: .12em; color: var(--dim); padding: 10px 8px 4px; }
.w-code { background: var(--bg); border-radius: 6px; padding: 8px 10px; font-size: 11px; color:#aab6c4; white-space: pre-wrap; word-break: break-word; }
.w-log { background:#1c1214; color:#f0a8a5; }
.w-lbl { font-size: 10px; letter-spacing: .1em; color: var(--dim); }
.w-btn-ok { background: #238636 !important; color: #fff !important; } .w-btn-ghost { background:#1b222c !important; border: 1px solid #263040; color: var(--text) !important; }
.w-freeze { background:#b62324 !important; color:#fff !important; font-weight: 700; }
.w-thaw { background:#d29922 !important; color:#0e1217 !important; font-weight: 700; }
.w-link { color: var(--text); text-decoration: none; } .w-link:hover { color: var(--accent); }
a { color: var(--accent); }
.w-act { grid-template-columns: 70px 104px minmax(0,1fr) 18px; } .w-tl { grid-template-columns: 70px 150px minmax(0,1fr) 90px; }
@media (max-width: 1023px) { .w-order-first { order: -1; } }
@media (max-width: 640px) { .w-kpis { grid-template-columns: repeat(2, minmax(0,1fr)) !important; } }
@media (max-width: 640px) { .w-act { grid-template-columns: 62px minmax(0,1fr) 18px; } .w-act > :nth-child(2) { grid-column: 2; } .w-act > :nth-child(3) { grid-column: 1 / 3; } .w-act > :nth-child(4) { grid-column: 3; grid-row: 1; }
  .w-tl { grid-template-columns: 62px minmax(0,1fr); } .w-tl > :nth-child(3), .w-tl > :nth-child(4) { grid-column: 1 / 3; } }
</style>
"""


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
    if s < 0:
        s = -s
        return f"in {int(s)} s" if s < 90 else f"in {int(s / 60)} min" if s < 5400 else f"in {s / 3600:.1f} h"
    return f"{int(s)} s ago" if s < 90 else f"{int(s / 60)} min ago" if s < 5400 else f"{s / 3600:.1f} h ago" if s < 172800 else f"{s / 86400:.0f} d ago"


def wib(x: Any, fmt: str = "%H:%M:%S") -> str:
    d = _dt(x)
    return d.astimezone(WIB).strftime(fmt) if d else "—"


def when(x: Any) -> str:
    return f"{wib(x, '%d %b %H:%M')} WIB · {rel(x)}"


def age_s(x: Any) -> float | None:
    d = _dt(x)
    return (_now() - d).total_seconds() if d else None


def usd(v: float, digits: int = 2) -> str:
    return f"${v:,.{digits}f}"


def _s(x: Any) -> str:
    return str(x).split(".")[-1]


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
            "EXECUTING": "det", "VERIFYING": "det", "DIAGNOSING": "llm", "DETECTED": "warn", "TRIAGED": "warn", "DECIDED": "det"}.get(state, "grey")


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
    """Flatten incident timelines, diagnoses and decision results into labelled events (who produced each step)."""
    dec_by_id = {d.decision_id: d for d in decs}
    out: list[dict] = []
    for inc in incs[:60]:
        for i, t in enumerate(inc.timeline):
            note = t.get("note", "") or ""
            actor = t.get("actor", "") or ""
            to = _s(t.get("to", ""))
            if actor.startswith("human"):
                label = "HUMAN"; kind = "ok"
            elif to == "DIAGNOSING":
                label = "EVIDENCE"; kind = "det"
            elif to == "DECIDED" or "NEED_APPROVAL" in note or note.startswith("re-evaluated"):
                label = "POLICY"; kind = "det"
            elif to == "EXECUTING":
                label = "EXECUTOR"; kind = "det"
            elif to in ("VERIFYING", "RESOLVED") and i > 0:
                label = "VERIFY"; kind = "det"
            else:
                label = "DETERMINISTIC"; kind = "det"
            out.append({"ts": t.get("ts"), "kind": kind, "label": label, "title": f"{inc.rule} · {inc.job_id or inc.instance_ref}",
                        "detail": note[:220], "inc": inc.incident_id, "ok": to not in ("ESCALATED", "FAILED_ACTION")})
        if inc.diagnosis:
            d = inc.diagnosis; cc = inc.crosscheck or {}
            model = str(d.get("model", "")).replace("gemini-", "").replace("-", " ").upper() or "3.5 FLASH"
            out.append({"ts": inc.updated_at.isoformat(), "kind": "llm", "label": f"GEMINI {model}",
                        "title": f"{d.get('category', '?')} · {d.get('transient_or_permanent', '')} · confidence {cc.get('adjusted_confidence', d.get('confidence', '?'))}",
                        "detail": f"proposes {d.get('recommended_action', '?')} · cost {usd(inc.llm_cost_usd, 3)} · falsifiable: {str(d.get('falsifiable_check', ''))[:120]}",
                        "inc": inc.incident_id, "ok": True})
            if cc:
                out.append({"ts": inc.updated_at.isoformat(), "kind": "det", "label": "CROSS-CHECK", "title": "Claims checked against numbers",
                            "detail": " · ".join(f"{c.get('check')} {'✓' if c.get('ok') else '✗'}" for c in cc.get("checks", []))[:220],
                            "inc": inc.incident_id, "ok": bool(cc.get("passed", True))})
        for did in inc.decision_ids:
            d = dec_by_id.get(did)
            if d and d.result:
                out.append({"ts": d.created_at.isoformat(), "kind": "det", "label": "VERIFY", "title": f"{d.action} · requested vs observed",
                            "detail": f"requested {d.result.get('requested')} → observed {d.result.get('observed') or d.result.get('error')}",
                            "inc": inc.incident_id, "ok": _s(d.status) == "DONE"})
    out.sort(key=lambda e: str(e["ts"]), reverse=True)
    return out[:limit]


NAV = [("OPERATIONS", None), ("Overview", "/"), ("Fleet", "/fleet"), ("Jobs", "/jobs"), ("Incidents", "/incidents"),
       ("CONTROL", None), ("Approvals", "/approvals"), ("Policies", "/policies"),
       ("RECORDS", None), ("Budget & ETTR", "/budget"), ("Audit", "/audit"), ("Health", "/health")]

LOGO = ('<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#7cc4ff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6l7-3z"></path><path d="M9 12l2 2 4-4"></path></svg>')


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
            with ui.column().classes("w-full gap-1 p-2 mt-auto").style("margin-top: auto"):
                ui.html('<div class="sec">WARDEN HEARTBEAT</div>')
                for src in ("watcher", "steward", "deadman"):
                    h = next((x for x in health if x["src"] == src), None)
                    a = age_s(h.get("last_ok_at")) if h else None
                    stale = a is None or a > STALE_HEALTH_S
                    with ui.row().classes("items-center justify-between w-full no-wrap"):
                        ui.label(src).classes("text-xs w-muted")
                        ui.label(("STALE · " if stale else "") + rel(h.get("last_ok_at") if h else None)).classes("text-xs num " + ("w-warn" if stale else "w-ok"))
    with ui.header(elevated=False).classes("items-center justify-between px-3 py-2 no-wrap"):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat dense round").classes("lt-md")
            with ui.column().classes("gap-0"):
                ui.label(title).classes("text-lg font-semibold leading-tight")
                ui.label(f"{wib(_now(), '%d %b %H:%M:%S')} WIB · reloads every {RELOAD_S} s").classes("text-xs w-muted")
        with ui.row().classes("items-center gap-2 no-wrap"):
            if frozen:
                tag("FROZEN · all actions at L0", "crit")
                ui.button("Thaw", on_click=lambda: act("/freeze", b"freeze", on="false", who="dashboard")).props("dense no-caps unelevated color=amber-8 text-color=black").classes("px-3 font-bold")
            else:
                ui.button("FREEZE", icon="pause_circle", on_click=lambda: act("/freeze", b"freeze", on="true", who="dashboard")).props("dense no-caps unelevated color=red-9").classes("px-3 font-bold").tooltip("Stop every autonomous action immediately")


def card_head(title: str, right: str = ""):
    with ui.element("div").classes("w-head"):
        ui.label(title)
        if right:
            ui.label(right).classes("text-xs w-muted font-normal text-right")


def kpi(k: str, v: str, s: str, cls: str = "", warn: bool = False):
    with ui.element("div").classes(("w-card-warn" if warn else "w-card") + " w-kpi"):
        ui.html(f'<div class="k">{k}</div><div class="v num {cls}">{v}</div><div class="s">{s}</div>')


def hb_status(h) -> tuple[str, str]:
    if not h:
        return "no heartbeat", "w-warn"
    a = age_s(h.ts)
    return (f"heartbeat {rel(h.ts)}", "w-ok") if a is not None and a <= STALE_HB_S else (f"STALE · heartbeat {rel(h.ts)}", "w-warn")


def chart_opts(hbs, contract: bool) -> dict:
    hbs = sorted(hbs, key=lambda h: h.ts)
    x = [wib(h.ts, "%H:%M") for h in hbs]
    if contract:
        series = [{"type": "line", "data": [h.step for h in hbs], "name": "step", "lineStyle": {"color": "#7cc4ff"}, "itemStyle": {"color": "#7cc4ff"}, "symbol": "none"},
                  {"type": "line", "yAxisIndex": 1, "data": [h.loss for h in hbs], "name": "loss", "lineStyle": {"color": "#d29922"}, "itemStyle": {"color": "#d29922"}, "symbol": "none"}]
        yaxes = [{"type": "value", "axisLabel": {"color": "#5f6f83"}, "splitLine": {"lineStyle": {"color": "#161d26"}}}, {"type": "value", "axisLabel": {"color": "#5f6f83"}, "splitLine": {"show": False}}]
    else:
        series = [{"type": "line", "data": [h.cpu_pct for h in hbs], "name": "cpu %", "lineStyle": {"color": "#7cc4ff"}, "itemStyle": {"color": "#7cc4ff"}, "symbol": "none"},
                  {"type": "line", "data": [h.gpu_util for h in hbs], "name": "gpu %", "lineStyle": {"color": "#3fb950"}, "itemStyle": {"color": "#3fb950"}, "symbol": "none"}]
        yaxes = [{"type": "value", "max": 100, "axisLabel": {"color": "#5f6f83"}, "splitLine": {"lineStyle": {"color": "#161d26"}}}]
    return {"backgroundColor": "transparent", "grid": {"left": 48, "right": 48, "top": 26, "bottom": 26}, "legend": {"textStyle": {"color": "#8797ab"}, "top": 0},
            "xAxis": {"type": "category", "data": x, "axisLabel": {"color": "#5f6f83"}}, "yAxis": yaxes, "series": series}


def heartbeat_chart(job_id: str, title: bool = True):
    hbs = db.recent_heartbeats(job_id, 60) if job_id else []
    if not hbs:
        return
    contract = any((not h.synthetic) and h.loss is not None for h in hbs)
    with ui.element("div").classes("px-3 pb-3"):
        if title:
            ui.label("training heartbeat · step & loss" if contract else "host heartbeat · cpu % & gpu % (legacy job: no step/loss from the trainer)").classes("text-xs w-dim pb-1")
        ui.echart(chart_opts(hbs, contract)).classes("w-full").style("height: 170px")


def approval_card(dec, inc, compact: bool = False):
    exp = dec.expires_at
    expired = bool(exp and exp < _now())
    did = dec.decision_id
    with ui.element("div").classes("w-card-warn p-3 flex flex-col gap-2 w-full"):
        with ui.row().classes("items-center justify-between w-full no-wrap"):
            ui.label(f"AWAITING APPROVAL · {_s(dec.autonomy)}").classes("text-xs w-warn tracking-wider")
            ui.label(("expired " + rel(exp)) if expired else ("expires " + rel(exp)) if exp else "").classes("text-xs num " + ("w-crit" if expired else "w-warn"))
        ui.link(f"{dec.action} · {dec.job_id or '—'}", f"/incidents/{dec.incident_id}").classes("font-semibold text-base w-link")
        ui.label(f"{dec.params.get('instance_ref') or '—'} · decision …{did[-8:]}").classes("text-xs w-muted")
        if inc and inc.diagnosis:
            d = inc.diagnosis; cc = inc.crosscheck or {}
            with ui.row().classes("items-center gap-2 no-wrap"):
                tag("GEMINI", "llm"); ui.label(f"{d.get('category')} · confidence {cc.get('adjusted_confidence', d.get('confidence'))} · {usd(inc.llm_cost_usd, 3)}").classes("text-sm")
        else:
            with ui.row().classes("items-center gap-2 no-wrap"):
                tag("DETERMINISTIC", "det"); ui.label(inc.rule if inc else dec.action).classes("text-sm")
        with ui.element("div").classes("grid grid-cols-3 gap-2"):
            conf = str((inc.crosscheck or {}).get("adjusted_confidence", (inc.diagnosis or {}).get("confidence", "rule"))) if inc else "rule"
            for k, v in (("CONFIDENCE", conf), ("BLAST RADIUS", _s(dec.blast_radius)), ("COST", usd(dec.cost_usd, 3))):
                ui.html(f'<div class="w-code"><div class="w-lbl">{k}</div><div class="font-semibold num">{v}</div></div>')
        if dec.explain and not compact:
            ui.html('<div class="w-lbl">POLICY TRACE</div><div class="w-code">' + "<br>".join(e for e in dec.explain[:6]) + "</div>")
        if inc and (inc.diagnosis or {}).get("evidence_quotes") and not compact:
            ui.html('<div class="w-lbl">EVIDENCE</div><div class="w-code w-log mono">' + "<br>".join(str(q)[:140] for q in inc.diagnosis["evidence_quotes"][:4]) + "</div>")
        plan = dec.dry_run_plan.get("plan") if dec.dry_run_plan else None
        if plan:
            ui.html('<div class="w-lbl">DRY-RUN · WHAT WILL HAPPEN</div><div class="w-code mono">' + json.dumps(plan, ensure_ascii=False, indent=1)[:900] + "</div>")
        if expired:
            ui.button("Re-evaluate with current context", on_click=lambda _, i=did: act(f"/decisions/{i}/reevaluate", i.encode(), who="dashboard")).props("no-caps unelevated color=blue-grey-9").classes("w-full").style("height:44px")
        else:
            with ui.element("div").classes("grid grid-cols-3 gap-2"):
                ui.button("Approve", on_click=lambda _, i=did: act(f"/decisions/{i}/approve", i.encode(), who="dashboard")).props("no-caps unelevated color=green-8").classes("font-bold").style("height:44px")
                ui.button("Deny", on_click=lambda _, i=did: act(f"/decisions/{i}/deny", i.encode(), who="dashboard")).props("no-caps unelevated color=blue-grey-9").style("height:44px")
                ui.button("Always 24h", on_click=lambda _, i=did: act(f"/decisions/{i}/always", i.encode(), who="dashboard")).props("no-caps unelevated color=blue-grey-9").style("height:44px").tooltip("Approve, and run this action at L2 for this job for the next 24 hours")


def incident_row(inc):
    with ui.element("div").classes("w-row px-3 py-2 grid gap-2 items-center cursor-pointer").style("grid-template-columns: 76px minmax(0,1fr) auto").on("click", lambda _, i=inc.incident_id: ui.navigate.to(f"/incidents/{i}")):
        tag(inc.severity.upper(), sev_kind(inc.severity))
        with ui.column().classes("gap-0 min-w-0"):
            ui.label(f"{inc.rule} · {inc.job_id or inc.instance_ref}").classes("font-semibold text-sm truncate w-full")
            ui.label(f"{wib(inc.created_at, '%H:%M')} · {rel(inc.created_at)} · burn {usd(inc.cost_burning_usd_per_hour, 3)}/h · LLM {usd(inc.llm_cost_usd, 3)}").classes("text-xs w-muted truncate w-full")
        tag(_s(inc.state), state_kind(_s(inc.state)))


def job_card(j, h, e: dict | None = None, inst=None):
    txt, cls = hb_status(h)
    with ui.element("div").classes("w-row px-3 py-2 flex flex-col gap-1"):
        with ui.row().classes("items-center justify-between w-full no-wrap"):
            with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
                ui.link(j.job_id, "/jobs").classes("font-semibold w-link truncate")
                ui.label("legacy · log parser" if j.legacy else "full contract").classes("text-xs w-dim")
            tag(f"{_s(j.status)}{(' · ' + j.phase) if j.phase else ''}", "ok" if _s(j.status) == "RUNNING" else "grey" if _s(j.status) == "COMPLETE" else "warn")
        if h and h.step is not None and not h.synthetic:
            ui.label(f"step {h.step:,}" + (f" · loss {h.loss:.4f}" if h.loss is not None else "") + f" · run {h.run_id or j.run_id or '—'}").classes("text-xs w-muted num truncate")
        elif h:
            ui.label(f"cpu {h.cpu_pct or 0:.0f} % · gpu {h.gpu_util if h.gpu_util is not None else '—'} · disk {h.disk_avail_gb or 0:.1f} GB · run {h.run_id or j.run_id or '—'}").classes("text-xs w-muted num truncate")
        with ui.row().classes("items-center justify-between w-full no-wrap"):
            ui.label(txt).classes("text-xs num " + cls)
            if e and e.get("ettr") is not None:
                ui.label(f"ETTR {e['ettr']:.2f} · {e.get('effective_h')} h ÷ {e.get('paid_h')} h").classes("text-xs w-dim num")
        extra = []
        if j.last_good_ckpt.get("path"):
            extra.append(f"last intact ckpt {j.last_good_ckpt['path']}")
        if inst:
            extra.append(f"{inst.name} · {'spot' if inst.spot else 'on-demand'} · {usd(inst.hourly_price_usd, 3)}/h · {_s(inst.status)}")
        if extra:
            ui.label(" · ".join(extra)).classes("text-xs w-dim truncate")


def health_grid(rows: list[dict], cols: int = 2):
    with ui.element("div").classes("grid").style(f"grid-template-columns: repeat({cols}, minmax(0,1fr))"):
        for r in sorted(rows, key=lambda x: x["src"]):
            a = age_s(r.get("last_ok_at"))
            stale = a is None or a > STALE_HEALTH_S
            with ui.element("div").classes("w-row px-3 py-2 flex items-center justify-between gap-2"):
                ui.label(r["src"]).classes("text-sm truncate")
                ui.label(("STALE · " if stale else "") + rel(r.get("last_ok_at")) + (f" · {r.get('consecutive_failures', 0)} fails" if r.get("consecutive_failures") else "")).classes("text-xs num " + ("w-warn" if stale else "w-ok"))


def cost_chart(proj: dict):
    days = sorted((d.to_dict() | {"day": d.id} for d in db.client().collection("costs").stream()), key=lambda x: x["day"])
    xs = [d["day"][5:] for d in days]
    cum, run = [], 0.0
    for d in days:
        run += float(d.get("compute_usd", 0.0)) + float(d.get("llm_usd", 0.0)); cum.append(round(run, 4))
    ui.echart({"backgroundColor": "transparent", "grid": {"left": 48, "right": 16, "top": 26, "bottom": 28},
               "xAxis": {"type": "category", "data": xs, "axisLabel": {"color": "#5f6f83"}},
               "yAxis": {"type": "log", "min": 0.01, "axisLabel": {"color": "#5f6f83", "formatter": "${value}"}, "splitLine": {"lineStyle": {"color": "#161d26"}}},
               "series": [{"type": "line", "name": "ledger · cumulative", "data": [max(c, 0.01) for c in cum], "lineStyle": {"color": "#7cc4ff", "width": 2}, "itemStyle": {"color": "#7cc4ff"}, "areaStyle": {"color": "rgba(124,196,255,0.08)"}},
                          {"type": "line", "name": "cap", "data": [proj["cap_usd"]] * len(xs), "lineStyle": {"color": "#3a4656", "type": "dashed"}, "itemStyle": {"color": "#3a4656"}, "symbol": "none"}],
               "legend": {"textStyle": {"color": "#8797ab"}, "right": 8, "top": 0}}).classes("w-full").style("height: 160px")


def auto_reload():
    ui.timer(RELOAD_S, ui.navigate.reload, once=True)


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
            kpi("JOBS WATCHED", str(len(jobs)), f"{sum(1 for j in jobs if _s(j.status) == 'RUNNING')} running · {sum(1 for j in jobs if _s(j.status) == 'COMPLETE')} complete")
            kpi("MACHINES UP", f"{len(running)}<span style='font-size:14px;color:var(--dim)'>/{len(insts)}</span>", "Compute Engine · label warden-managed")
            kpi("OPEN INCIDENTS", str(len(open_incs)), f"{len(resolved_today)} resolved today", "w-warn" if open_incs else "")
            kpi("AWAITING APPROVAL", str(len(pending)), ("soonest expires " + rel(soonest)) if soonest else "nothing pending", "w-warn" if pending else "", warn=bool(pending))
            kpi("FLEET ETTR · 7 D", f"{(eff / paid):.2f}" if paid else "—", f"effective {eff:.2f} h ÷ paid {paid:.2f} h · from heartbeats")
            kpi("COST TODAY", usd(proj["today_usd"]), f"cap {usd(proj['cap_usd'], 0)} · LLM {usd(float(data['today'].get('llm_usd', 0.0)), 3)} · 30 d at this burn {usd(proj['if_left_running_30d_usd'])}")
        with ui.element("div").classes("grid gap-3 w-full items-start").style("grid-template-columns: repeat(auto-fit, minmax(340px, 1fr))"):
            with ui.element("div").classes("w-card"):
                with ui.element("div").classes("w-head"):
                    ui.label("Warden activity")
                    ui.html('<span class="text-xs w-muted font-normal"><span class="w-tag t-det">DETERMINISTIC</span> &nbsp;<span class="w-tag t-llm">GEMINI</span></span>')
                evs = activity(incs, data["decs"], 14)
                if not evs:
                    ui.label("No activity yet.").classes("w-dim p-4 text-sm")
                for e in evs:
                    with ui.element("div").classes("w-row w-act px-3 py-2 grid gap-2 items-start cursor-pointer").on("click", lambda _, i=e["inc"]: ui.navigate.to(f"/incidents/{i}")):
                        ui.html(f'<div class="num text-xs w-muted">{wib(e["ts"])}<br><span class="w-dim">{rel(e["ts"])}</span></div>')
                        tag(e["label"], e["kind"])
                        with ui.column().classes("gap-0 min-w-0"):
                            ui.label(e["title"]).classes("text-sm font-semibold truncate w-full")
                            ui.label(e["detail"]).classes("text-xs w-muted w-full")
                        ui.html('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="%s" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle>%s</svg>'
                                % (("#3fb950", '<path d="M8 12l3 3 5-6"></path>') if e["ok"] else ("#f85149", '<path d="M9 9l6 6M15 9l-6 6"></path>')))
            with ui.column().classes("gap-3 w-order-first w-full"):
                if pending:
                    for d in pending[:2]:
                        approval_card(d, db.incidents.get(d.incident_id) if d.incident_id else None, compact=True)
                    if len(pending) > 2:
                        ui.link(f"{len(pending) - 2} more awaiting approval", "/approvals").classes("text-sm")
                else:
                    with ui.element("div").classes("w-card p-3 w-full"):
                        ui.label("Nothing awaiting approval").classes("text-sm w-muted")
                with ui.element("div").classes("w-card w-full"):
                    card_head("Recent incidents", f"{len(incs)} total")
                    if not incs:
                        ui.label("No incidents recorded.").classes("w-dim p-4 text-sm")
                    for inc in incs[:6]:
                        incident_row(inc)
            with ui.column().classes("gap-3 w-full"):
                with ui.element("div").classes("w-card w-full"):
                    card_head("Jobs", "heartbeat · ETTR 7 d")
                    if not jobs:
                        ui.label("No jobs registered.").classes("w-dim p-4 text-sm")
                    for j in sorted(jobs, key=lambda x: (_s(x.status) != "RUNNING", x.job_id)):
                        job_card(j, hb.get(j.job_id), ettrs.get(j.job_id), inst_by_job.get(j.job_id))
                with ui.element("div").classes("w-card w-full"):
                    card_head("Audit · intent vs result", "append-only")
                    rows = [d.to_dict() for d in db.client().collection("audit").order_by("ts", direction="DESCENDING").limit(40).stream()]
                    res = [r for r in rows if r.get("phase") == "result"][:5]
                    if not res:
                        ui.label("No actions yet.").classes("w-dim p-4 text-sm")
                    for r in res:
                        ok = r.get("ok")
                        with ui.element("div").classes("w-row px-3 py-2 grid gap-2 items-center text-xs num").style("grid-template-columns: 40px minmax(0,1fr) minmax(0,1fr) 52px"):
                            ui.label(wib(r.get("ts"), "%H:%M")).classes("w-muted")
                            ui.label(f"{r.get('action')} {str(r.get('target', ''))[-22:]}").classes("truncate")
                            ui.label((str((r.get("after") or {}).get("observed") or "")[:40] or "done") if ok else str(r.get("error", ""))[:40]).classes("truncate " + ("w-ok" if ok else "w-warn"))
                            ui.label(str(r.get("actor", "")).split(":")[0]).classes("w-muted truncate")
                with ui.element("div").classes("w-card w-full"):
                    card_head("Signal health", "stale = do not trust")
                    health_grid(data["health"])
        with ui.element("div").classes("w-card p-3 grid gap-3 w-full").style("grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))"):
            with ui.column().classes("gap-2"):
                ui.label("Budget · ledger vs cap").classes("font-semibold")
                with ui.element("div").classes("grid grid-cols-2 gap-2"):
                    for k, v in (("LEDGER MTD", usd(proj["month_to_date_usd"])), ("BURN NOW", f"{usd(proj['burn_usd_per_hour'], 3)}/h"), ("CAP", usd(proj["cap_usd"], 0)),
                                 ("RUNWAY", f"{proj['runway_days']} d" if proj["runway_days"] else "∞ · no burn")):
                        ui.html(f'<div><div class="w-lbl">{k}</div><div class="num text-lg font-semibold">{v}</div></div>')
                ui.label("kill-switch at 50 / 80 / 100 % of cap").classes("text-xs w-dim")
                ui.button("Run steward sweep now", on_click=lambda: (notify_result(core("/steward")), ui.timer(0.9, ui.navigate.reload, once=True))).props("no-caps dense unelevated color=blue-grey-9")
            cost_chart(proj)
        ui.label("every action leaves an audit entry · delete is not an action · STOP, never DELETE").classes("text-xs w-dim")
    auto_reload()


@ui.page("/fleet")
def fleet():
    data = load_all()
    shell("Fleet", "/fleet", data)
    insts = sorted(data["insts"], key=lambda x: x.ref)
    with ui.column().classes("w-full gap-3 p-4"):
        if not insts:
            with ui.element("div").classes("w-card p-4"):
                ui.label("No machines labelled warden-managed=true.").classes("w-muted text-sm")
        for i in insts:
            h = data["hb"].get(i.job_id) if i.job_id else None
            txt, cls = hb_status(h)
            with ui.element("div").classes("w-card p-3 flex flex-col gap-1 w-full"):
                with ui.row().classes("items-center justify-between w-full no-wrap"):
                    ui.label(i.name).classes("font-semibold")
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        if i.termination_action == "DELETE" or i.boot_disk_auto_delete:
                            tag("UNSAFE CONFIG", "crit")
                        tag(_s(i.status), "ok" if _s(i.status) == "RUNNING" else "grey")
                ui.label(f"{i.zone} · {i.machine_type} · {'spot' if i.spot else 'on-demand'} · {usd(i.hourly_price_usd, 3)}/h · job {i.job_id or '—'} · on termination: {i.termination_action or '—'}").classes("text-xs w-muted")
                with ui.row().classes("items-center gap-3 no-wrap"):
                    ui.label(txt).classes("text-xs num " + cls)
                    if h:
                        ui.label(f"phase {h.phase or '—'} · step {h.step if h.step is not None else '—'} · cpu {h.cpu_pct or 0:.0f} % · gpu {h.gpu_util if h.gpu_util is not None else '—'} · disk {h.disk_avail_gb or 0:.1f} GB").classes("text-xs w-muted num truncate")
                ui.label(f"seen {when(i.last_seen)}" + (f" · operator session until {wib(i.operator_active_until)}" if i.operator_active_until and i.operator_active_until > _now() else "")).classes("text-xs w-dim")
    auto_reload()


@ui.page("/jobs")
def jobs_page():
    data = load_all()
    shell("Jobs", "/jobs", data)
    from warden.steward import ledger
    inst_by_job = {i.job_id: i for i in data["insts"] if i.job_id}
    with ui.column().classes("w-full gap-3 p-4"):
        if not data["jobs"]:
            with ui.element("div").classes("w-card p-4"):
                ui.label("No jobs registered.").classes("w-muted text-sm")
        for j in sorted(data["jobs"], key=lambda x: (_s(x.status) != "RUNNING", x.job_id)):
            with ui.element("div").classes("w-card w-full"):
                job_card(j, data["hb"].get(j.job_id), ledger.ettr(j.job_id, 168), inst_by_job.get(j.job_id))
                heartbeat_chart(j.job_id)
                ui.label(f"run {j.run_id or '—'} · expect {json.dumps(j.expect)[:120] if j.expect else '—'} · spent {usd(j.spent_usd, 3)}"
                         + (f" · operator hold until {wib(j.operator_hold_until)}" if j.operator_hold_until and j.operator_hold_until > _now() else "")).classes("text-xs w-dim px-3 pb-3")
    auto_reload()


@ui.page("/incidents")
def incidents():
    data = load_all()
    shell("Incidents", "/incidents", data)
    incs = data["incs"]
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("w-card w-full"):
            card_head("All incidents", f"{len(incs)} · newest first")
            if not incs:
                ui.label("No incidents recorded.").classes("w-dim p-4 text-sm")
            for inc in incs[:150]:
                incident_row(inc)
    auto_reload()


@ui.page("/incidents/{incident_id}")
def incident_detail(incident_id: str):
    inc = db.incidents.get(incident_id)
    shell(f"Incident …{incident_id[-10:]}", "/incidents")
    if not inc:
        ui.label("Incident not found.").classes("w-muted p-4"); return
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("w-card p-3 flex flex-col gap-1 w-full"):
            with ui.row().classes("items-center gap-2"):
                tag(_s(inc.state), state_kind(_s(inc.state))); tag(inc.severity.upper(), sev_kind(inc.severity)); tag("RULE · DETERMINISTIC", "det")
            ui.label(inc.summary).classes("font-semibold")
            ui.label(f"rule {inc.rule} · job {inc.job_id or '—'} · machine {inc.instance_ref or '—'} · opened {when(inc.created_at)} · updated {rel(inc.updated_at)} · burn {usd(inc.cost_burning_usd_per_hour, 3)}/h · LLM {usd(inc.llm_cost_usd, 3)}").classes("text-xs w-muted")
        for did in inc.decision_ids:
            dec = db.decisions.get(did)
            if dec and _s(dec.status) == "PENDING" and _s(dec.verdict) == "NEED_APPROVAL":
                approval_card(dec, inc)
        if inc.diagnosis:
            d = inc.diagnosis; cc = inc.crosscheck or {}
            with ui.element("div").classes("w-card w-full"):
                with ui.element("div").classes("w-head"):
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        tag("GEMINI", "llm"); ui.label("Diagnosis + cross-check")
                    ui.label(f"cost {usd(inc.llm_cost_usd, 3)}").classes("text-xs w-muted font-normal")
                with ui.column().classes("p-3 gap-2"):
                    ui.label(d.get("human_summary") or d.get("human_summary_id") or "").classes("text-sm")
                    ui.label(f"{d.get('category')} · {d.get('transient_or_permanent')} · confidence {cc.get('adjusted_confidence', d.get('confidence'))} · proposes {d.get('recommended_action')} · needs human: {d.get('needs_human')}").classes("text-sm w-muted")
                    tag("CROSS-CHECK PASSED" if cc.get("passed") else "CROSS-CHECK FAILED", "ok" if cc.get("passed") else "crit")
                    for c in cc.get("checks", []):
                        ui.label(f"{'✓' if c.get('ok') else '✗'} {c.get('check')} {c.get('note', '')}").classes("text-xs " + ("w-ok" if c.get("ok") else "w-crit"))
                    if d.get("evidence_quotes"):
                        ui.html('<div class="w-lbl">EVIDENCE LINES</div><div class="w-code w-log mono">' + "<br>".join(str(q)[:200] for q in d["evidence_quotes"]) + "</div>")
                    if d.get("falsifiable_check"):
                        ui.label(f"falsifiable check: {d['falsifiable_check']}").classes("text-xs w-dim")
        for eid in inc.evidence_ids:
            ev = db.evidence.get(eid)
            if not ev:
                continue
            with ui.element("div").classes("w-card w-full"):
                card_head(f"Evidence · {ev.kind}", when(ev.created_at))
                with ui.column().classes("p-3 gap-1"):
                    if ev.kind == "artifact_check":
                        for r in ev.payload.get("results", []):
                            ui.label(f"{'✓' if r.get('ok') else '✗'} {r.get('name')} · {r.get('bytes', 0):,} B · {r.get('reason', '')}").classes("text-xs mono " + ("w-ok" if r.get("ok") else "w-crit"))
                    else:
                        ui.label(ev.summary).classes("text-sm")
                        if ev.payload:
                            ui.html('<div class="w-code mono">' + json.dumps(ev.payload, ensure_ascii=False, indent=1)[:1500] + "</div>")
        hbs = db.recent_heartbeats(inc.job_id, 60) if inc.job_id else []
        if hbs:
            contract = any((not h.synthetic) and h.loss is not None for h in hbs)
            with ui.element("div").classes("w-card w-full"):
                card_head("Heartbeat · last 60", "training: step & loss" if contract else "host: cpu % & gpu % (legacy job: no step/loss from the trainer)")
                ui.echart(chart_opts(hbs, contract)).classes("w-full").style("height: 180px")
        for did in inc.decision_ids:
            dec = db.decisions.get(did)
            if not dec or (_s(dec.status) == "PENDING" and _s(dec.verdict) == "NEED_APPROVAL"):
                continue
            with ui.element("div").classes("w-card w-full"):
                with ui.element("div").classes("w-head"):
                    ui.label(f"{dec.action} · {_s(dec.autonomy)} · {_s(dec.verdict)}")
                    tag(_s(dec.status), "ok" if _s(dec.status) == "DONE" else "crit" if _s(dec.status) in ("FAILED", "EXPIRED") else "grey")
                with ui.column().classes("p-3 gap-1"):
                    ui.label(f"blast radius {_s(dec.blast_radius)} · cost {usd(dec.cost_usd, 3)} · {when(dec.created_at)}" + (f" · expires {when(dec.expires_at)}" if dec.expires_at else "") + (f" · by {dec.approved_by}" if dec.approved_by else "")).classes("text-xs w-muted")
                    for e in dec.explain:
                        ui.label(f"• {e}").classes("text-xs")
                    if dec.dry_run_plan.get("plan"):
                        ui.html('<div class="w-lbl">DRY-RUN</div><div class="w-code mono">' + json.dumps(dec.dry_run_plan["plan"], ensure_ascii=False, indent=1)[:900] + "</div>")
                    if dec.result:
                        ui.label(f"requested {dec.result.get('requested')} → observed {dec.result.get('observed') or dec.result.get('error')}").classes("text-xs " + ("w-ok" if _s(dec.status) == "DONE" else "w-crit"))
                    if _s(dec.status) in ("EXPIRED", "REJECTED", "FAILED"):
                        ui.button("Re-evaluate with current context", on_click=lambda _, i=dec.decision_id: act(f"/decisions/{i}/reevaluate", i.encode(), who="dashboard")).props("no-caps dense unelevated color=blue-grey-9")
        with ui.element("div").classes("w-card w-full"):
            card_head("Timeline", f"{len(inc.timeline)} steps")
            for t in inc.timeline:
                with ui.element("div").classes("w-row w-tl px-3 py-2 grid gap-2 items-start text-xs"):
                    ui.html(f'<div class="num w-muted">{wib(t.get("ts"))}<br><span class="w-dim">{rel(t.get("ts"))}</span></div>')
                    ui.label(f"{_s(t.get('from', ''))} → {_s(t.get('to', ''))}").classes("num")
                    ui.label(t.get("note", "")).classes("w-muted")
                    ui.label(t.get("actor", "")).classes("w-dim truncate")


@ui.page("/approvals")
def approvals_page():
    data = load_all()
    shell("Approvals", "/approvals", data)
    decs, pending = data["decs"], data["pending"]
    stale = [d for d in decs if _s(d.status) in ("EXPIRED", "REJECTED", "FAILED")][:10]
    with ui.column().classes("w-full gap-3 p-4"):
        if not pending:
            with ui.element("div").classes("w-card p-4 w-full"):
                ui.label("Nothing awaiting approval.").classes("w-muted text-sm")
        for d in pending:
            approval_card(d, db.incidents.get(d.incident_id) if d.incident_id else None)
        if stale:
            with ui.element("div").classes("w-card w-full"):
                card_head("Expired · rejected · failed", "re-evaluate runs the policy again with the current context")
                for d in stale:
                    with ui.element("div").classes("w-row px-3 py-2 grid gap-2 items-center").style("grid-template-columns: minmax(0,1fr) auto"):
                        with ui.column().classes("gap-0 min-w-0"):
                            ui.link(f"{d.action} · {d.job_id or '—'} · {_s(d.status)}", f"/incidents/{d.incident_id}").classes("text-sm font-semibold w-link")
                            ui.label(f"{when(d.created_at)} · {_s(d.autonomy)} · {(d.explain[-1] if d.explain else '')[:90]}").classes("text-xs w-muted truncate")
                        ui.button("Re-evaluate", on_click=lambda _, i=d.decision_id: act(f"/decisions/{i}/reevaluate", i.encode(), who="dashboard")).props("no-caps dense unelevated color=blue-grey-9")
        ov = [x.to_dict() | {"id": x.id} for x in db.client().collection("policy_overrides").stream()]
        with ui.element("div").classes("w-card w-full"):
            card_head("Active overrides · Always 24h", str(len(ov)))
            if not ov:
                ui.label("None.").classes("w-dim p-3 text-sm")
            for o in ov:
                until = datetime.fromtimestamp(float(o.get("until", 0)), tz=timezone.utc)
                ui.label(f"{o['id']} → {o.get('level')} · by {o.get('by')} · until {wib(until, '%d %b %H:%M')} WIB ({rel(until)})").classes("text-sm px-3 py-2 w-row")
    auto_reload()


@ui.page("/policies")
def policies():
    from warden.policy.engine import load_policy
    data = load_all()
    shell("Policies", "/policies", data)
    pol = load_policy()
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("w-card w-full"):
            card_head("Autonomy per action", "L0 observe · L1 propose · L2 act then report · L3 act silently")
            with ui.element("div").classes("grid text-xs px-3 py-1 w-dim").style("grid-template-columns: 180px 60px minmax(0,1fr)"):
                ui.label("ACTION"); ui.label("LEVEL"); ui.label("LIMITS")
            for a, l in pol["autonomy"].items():
                with ui.element("div").classes("w-row grid px-3 py-2 text-sm items-center").style("grid-template-columns: 180px 60px minmax(0,1fr)"):
                    ui.label(a).classes("mono"); tag(str(l), "ok" if str(l) in ("L2", "L3") else "warn" if str(l) == "L1" else "grey")
                    ui.label(json.dumps(pol["limits"].get(a, {})) if pol["limits"].get(a) else "—").classes("text-xs w-muted mono")
        with ui.element("div").classes("grid gap-3 w-full").style("grid-template-columns: repeat(auto-fit, minmax(300px, 1fr))"):
            for title, obj in (("Global caps", pol["global"]), ("Circuit breaker", pol["circuit_breaker"]), ("Hard deny · never, by anyone, through Warden", pol["hard_deny"])):
                with ui.element("div").classes("w-card"):
                    card_head(title)
                    ui.html('<div class="w-code mono m-3">' + json.dumps(obj, indent=1) + "</div>")


@ui.page("/budget")
def budget():
    from warden.steward import ledger
    data = load_all()
    shell("Budget & ETTR", "/budget", data)
    p = data["proj"]
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("grid gap-3 w-full w-kpis").style("grid-template-columns: repeat(auto-fit, minmax(160px, 1fr))"):
            kpi("TODAY", usd(p["today_usd"]), "compute + LLM · ledger")
            kpi("MONTH TO DATE", usd(p["month_to_date_usd"]), f"cap {usd(p['cap_usd'], 0)} · {100 * p['month_to_date_usd'] / max(p['cap_usd'], 1):.1f} %")
            kpi("BURN NOW", f"{usd(p['burn_usd_per_hour'], 3)}/h", "running managed machines")
            kpi("RUNWAY", f"{p['runway_days']} d" if p["runway_days"] else "∞", "at current burn")
            kpi("IF LEFT 30 DAYS", usd(p["if_left_running_30d_usd"]), "kill-switch 50 / 80 / 100 %")
        with ui.element("div").classes("w-card p-3 w-full"):
            ui.label("Cumulative ledger vs cap").classes("font-semibold pb-1")
            cost_chart(p)
        with ui.element("div").classes("w-card w-full"):
            card_head("ETTR per job · 7 days", "effective training time ÷ paid machine time")
            for j in data["jobs"]:
                e = ledger.ettr(j.job_id, 168)
                with ui.element("div").classes("w-row px-3 py-2 grid gap-2 items-center").style("grid-template-columns: minmax(0,1fr) 70px minmax(0,1fr)"):
                    ui.label(f"{j.job_id} · {_s(j.status)} · spent {usd(j.spent_usd, 3)}").classes("text-sm truncate")
                    ui.label(f"{e['ettr']:.2f}" if e.get("ettr") is not None else "—").classes("num font-semibold")
                    ui.label(f"{e.get('effective_h', '—')} h effective ÷ {e.get('paid_h', '—')} h paid" if e.get("ettr") is not None else str(e.get("note", ""))).classes("text-xs w-muted num truncate")
        ui.button("Run steward sweep now", on_click=lambda: (notify_result(core("/steward")), ui.timer(0.9, ui.navigate.reload, once=True))).props("no-caps unelevated color=blue-grey-9")
    auto_reload()


@ui.page("/audit")
def audit():
    data = load_all()
    shell("Audit", "/audit", data)
    rows = [d.to_dict() for d in db.client().collection("audit").order_by("ts", direction="DESCENDING").limit(300).stream()]
    cols = "grid-template-columns: 120px 64px 56px 140px minmax(0,1fr) minmax(0,1fr)"
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("w-card w-full").style("overflow-x: auto"):
            card_head("Append-only audit log", f"{len(rows)} entries · intent before, result after")
            with ui.element("div").classes("grid text-xs px-3 py-1 w-dim").style(cols + "; min-width: 720px"):
                for h in ("WHEN", "ACTOR", "PHASE", "ACTION", "TARGET", "RESULT"):
                    ui.label(h)
            if not rows:
                ui.label("No actions yet.").classes("w-dim p-4 text-sm")
            for r in rows:
                ok = r.get("ok")
                with ui.element("div").classes("w-row grid px-3 py-2 text-xs items-center num").style(cols + "; min-width: 720px"):
                    ui.label(wib(r.get("ts"), "%d %b %H:%M:%S")).classes("w-muted")
                    ui.label(str(r.get("actor", "")).split(":")[0])
                    ui.label(str(r.get("phase", "")))
                    ui.label(str(r.get("action", ""))).classes("mono truncate")
                    ui.label(str(r.get("target", ""))).classes("truncate w-muted")
                    ui.label("—" if ok is None else (str((r.get("after") or {}).get("observed") or "ok")[:60] if ok else "✗ " + str(r.get("error", ""))[:60])).classes("truncate " + ("" if ok is None else "w-ok" if ok else "w-crit"))


@ui.page("/health")
def health():
    data = load_all()
    shell("Health", "/health", data)
    rows = data["health"]
    with ui.column().classes("w-full gap-3 p-4"):
        with ui.element("div").classes("w-card w-full"):
            card_head("Signal sources", "stale after 15 min without an OK · the deadman watches the watcher from outside")
            if not rows:
                ui.label("No health records yet.").classes("w-dim p-4 text-sm")
            for r in sorted(rows, key=lambda x: x["src"]):
                a = age_s(r.get("last_ok_at")); stale = a is None or a > STALE_HEALTH_S
                with ui.element("div").classes("w-row px-3 py-2 flex flex-col gap-1"):
                    with ui.row().classes("items-center justify-between w-full no-wrap"):
                        ui.label(r["src"]).classes("font-semibold text-sm")
                        tag("STALE" if stale else "ALIVE", "warn" if stale else "ok")
                    ui.label(f"last OK {when(r.get('last_ok_at'))} · consecutive failures {r.get('consecutive_failures', 0)}" + (f" · {str(r.get('last_error', ''))[:120]}" if r.get("last_error") else "")).classes("text-xs w-muted")
                    if r.get("stats"):
                        ui.label(json.dumps(r["stats"])).classes("text-xs w-dim mono")
    auto_reload()


def run():
    ui.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), title="Warden", reload=False, show=False, dark=True,
           storage_secret=os.environ.get("WARDEN_UI_SECRET", "dev"))


if __name__ in {"__main__", "__mp_main__"}:
    run()
