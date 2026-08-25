"""warden-ui (Fase 8): dashboard NiceGUI, mobile-first 390 px. Sumber data Firestore (polling 3 dtk — listener di Fase 12).
Halaman: Fleet · Incidents · Incident detail · Budget · Policies · Audit · Health. Tombol merah FREEZE di header.
Tanpa merek lain. Waktu WIB + relatif. Setiap klaim membawa bukti + biaya."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from nicegui import app, ui

from warden.config import settings
from warden.signals.ingest import sign
from warden.store import firestore as db

CORE = os.environ.get("WARDEN_CORE_URL", "http://127.0.0.1:18090")
WIB = timezone(timedelta(hours=7))
SEV = {"critical": "red", "warning": "amber", "info": "green"}
STATE_COLOR = {"RESOLVED": "green", "AWAITING_APPROVAL": "amber", "HELD": "blue-grey", "ESCALATED": "red", "FAILED_ACTION": "red",
               "EXECUTING": "blue", "DIAGNOSING": "purple", "DETECTED": "orange", "TRIAGED": "orange", "CLOSED": "grey"}


def _t(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    age = (datetime.now(timezone.utc) - d).total_seconds()
    rel = f"{int(age)} dtk" if age < 90 else f"{int(age/60)} mnt" if age < 5400 else f"{age/3600:.1f} jam"
    return f"{d.astimezone(WIB):%d %b %H:%M} WIB · {rel} lalu"


def _core(path: str, body_key: bytes | None = None, **params) -> dict:
    try:
        r = httpx.post(f"{CORE}{path}", params=params, headers={"X-Warden-Signature": sign(body_key or b"")}, timeout=20)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def header(title: str):
    frozen = db.client().collection("policies").document("runtime").get()
    fz = bool(frozen.exists and frozen.to_dict().get("frozen"))
    with ui.header().classes("items-center justify-between px-3 py-2"):
        with ui.row().classes("items-center gap-3"):
            ui.link("WARDEN", "/").classes("text-lg font-bold text-white no-underline")
            for name, path in (("Fleet", "/"), ("Insiden", "/incidents"), ("Anggaran", "/budget"), ("Kebijakan", "/policies"), ("Audit", "/audit"), ("Kesehatan", "/health")):
                ui.link(name, path).classes("text-white no-underline text-sm")
        with ui.row().classes("items-center gap-2"):
            if fz:
                ui.badge("DIBEKUKAN", color="red").props("outline")
                ui.button("Lepas", on_click=lambda: (_core("/freeze", b"freeze", on="false", who="dashboard"), ui.navigate.reload())).props("dense color=orange")
            else:
                ui.button("FREEZE", on_click=lambda: (_core("/freeze", b"freeze", on="true", who="dashboard"), ui.navigate.reload())).props("dense color=red icon=pause_circle").tooltip("Tombol merah: semua tindakan otomatis berhenti seketika")
    ui.label(title).classes("text-xl font-semibold px-3 pt-2")


def _empty(text: str):
    ui.label(text).classes("text-grey px-3 py-6 italic")


@ui.page("/")
def fleet():
    header("Armada")
    insts = db.fleet.list(limit=200)
    if not insts:
        _empty("Belum ada mesin berlabel warden-managed=true. Pasang harness (harness/install.sh) atau buat mesin lewat infra/vm_create.sh.")
    with ui.column().classes("w-full px-3 gap-2"):
        for i in sorted(insts, key=lambda x: x.ref):
            hb = db.last_heartbeat(i.job_id) if i.job_id else None
            age = (datetime.now(timezone.utc) - hb.ts).total_seconds() if hb else None
            stale = age is None or age > 180
            with ui.card().classes("w-full p-3"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(f"{i.name}").classes("font-semibold")
                    ui.badge(str(i.status), color="green" if i.status == "RUNNING" else "grey")
                ui.label(f"{i.zone} · {i.machine_type} · {'spot' if i.spot else 'on-demand'} · ${i.hourly_price_usd:.3f}/jam · job {i.job_id or '—'}").classes("text-sm text-grey")
                with ui.row().classes("items-center gap-2"):
                    ui.badge("denyut " + (f"{int(age)} dtk" if age is not None else "—"), color="amber" if stale else "green")
                    if hb:
                        ui.label(f"fase {hb.phase or '—'} · step {hb.step if hb.step is not None else '—'} · cpu {hb.cpu_pct or 0:.0f}% · gpu {hb.gpu_util if hb.gpu_util is not None else '—'} · disk {hb.disk_avail_gb or 0:.1f} GB").classes("text-sm")
                if i.termination_action == "DELETE" or i.boot_disk_auto_delete:
                    ui.badge("KONFIGURASI TIDAK AMAN", color="red")


@ui.page("/incidents")
def incidents():
    header("Insiden")
    incs = sorted(db.incidents.list(limit=200), key=lambda x: x.created_at, reverse=True)
    if not incs:
        _empty("Belum ada insiden — bagus, atau Watcher belum berjalan (cek Kesehatan).")
    with ui.column().classes("w-full px-3 gap-2"):
        for inc in incs[:100]:
            with ui.card().classes("w-full p-3 cursor-pointer").on("click", lambda _, i=inc.incident_id: ui.navigate.to(f"/incidents/{i}")):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(f"{inc.rule} · {inc.instance_ref or inc.job_id}").classes("font-semibold")
                    ui.badge(str(inc.state), color=STATE_COLOR.get(str(inc.state), "grey"))
                ui.label(inc.summary[:160]).classes("text-sm")
                ui.label(f"{_t(inc.created_at.isoformat())} · bakar ${inc.cost_burning_usd_per_hour:.3f}/jam · LLM ${inc.llm_cost_usd:.3f}").classes("text-xs text-grey")


@ui.page("/incidents/{incident_id}")
def incident_detail(incident_id: str):
    inc = db.incidents.get(incident_id)
    header(f"Insiden {incident_id}")
    if not inc:
        _empty("Insiden tidak ditemukan."); return
    with ui.column().classes("w-full px-3 gap-3"):
        with ui.card().classes("w-full p-3"):
            ui.badge(str(inc.state), color=STATE_COLOR.get(str(inc.state), "grey"))
            ui.label(inc.summary).classes("font-semibold")
            ui.label(f"aturan {inc.rule} · {inc.severity} · job {inc.job_id} · mesin {inc.instance_ref} · dibuat {_t(inc.created_at.isoformat())}").classes("text-sm text-grey")
        hbs = db.recent_heartbeats(inc.job_id, 60) if inc.job_id else []
        if hbs:
            with ui.card().classes("w-full p-3"):
                ui.label("Denyut (60 terakhir)").classes("font-semibold")
                ui.echart({"xAxis": {"type": "category", "data": [h.ts.astimezone(WIB).strftime("%H:%M") for h in hbs]},
                           "yAxis": [{"type": "value", "name": "step"}, {"type": "value", "name": "loss"}],
                           "series": [{"type": "line", "data": [h.step or 0 for h in hbs], "name": "step"},
                                      {"type": "line", "yAxisIndex": 1, "data": [h.loss for h in hbs], "name": "loss"}],
                           "legend": {}, "grid": {"left": 40, "right": 40, "top": 30, "bottom": 30}}).classes("w-full h-48")
        if inc.diagnosis:
            d = inc.diagnosis; cc = inc.crosscheck or {}
            with ui.card().classes("w-full p-3"):
                ui.label("Diagnosis (Gemini) + cek silang").classes("font-semibold")
                ui.label(d.get("human_summary_id", "")).classes("text-sm")
                ui.label(f"{d.get('category')} · confidence {cc.get('adjusted_confidence', d.get('confidence'))} · {d.get('transient_or_permanent')} · usul {d.get('recommended_action')}").classes("text-sm")
                ui.badge("cek silang LOLOS" if cc.get("passed") else "cek silang GAGAL", color="green" if cc.get("passed") else "red")
                for c in cc.get("checks", []):
                    ui.label(f"{'✅' if c['ok'] else '❌'} {c['check']} {c.get('note','')}").classes("text-xs")
                if d.get("evidence_quotes"):
                    ui.code("\n".join(d["evidence_quotes"])).classes("w-full text-xs")
                ui.label(f"cara membantah: {d.get('falsifiable_check','')}").classes("text-xs text-grey")
        for eid in inc.evidence_ids:
            ev = db.evidence.get(eid)
            if ev and ev.kind == "artifact_check":
                with ui.card().classes("w-full p-3"):
                    ui.label("Verifikasi artefak").classes("font-semibold")
                    for r in ev.payload.get("results", []):
                        ui.label(f"{'✅' if r['ok'] else '❌'} {r['name']} · {r.get('bytes',0)} B · {r.get('reason','')}").classes("text-sm")
        for did in inc.decision_ids:
            dec = db.decisions.get(did)
            if not dec:
                continue
            with ui.card().classes("w-full p-3"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(f"{dec.action} · {dec.autonomy} · {dec.verdict}").classes("font-semibold")
                    ui.badge(str(dec.status), color="green" if dec.status == "DONE" else "amber" if dec.status == "PENDING" else "grey")
                ui.label(f"blast radius {dec.blast_radius} · biaya ${dec.cost_usd:.2f}" + (f" · kedaluwarsa {_t(dec.expires_at.isoformat())}" if dec.expires_at else "")).classes("text-xs text-grey")
                for e in dec.explain:
                    ui.label(f"• {e}").classes("text-xs")
                if dec.dry_run_plan.get("plan"):
                    ui.code(json.dumps(dec.dry_run_plan["plan"], ensure_ascii=False, indent=1)).classes("w-full text-xs")
                if dec.result:
                    ui.label(f"hasil: diminta {dec.result.get('requested')} → terjadi {dec.result.get('observed') or dec.result.get('error')}").classes("text-xs")
                if dec.status == "PENDING" and dec.verdict == "NEED_APPROVAL":
                    with ui.row().classes("gap-3 pt-2"):
                        ui.button("Approve", on_click=lambda _, i=did: (ui.notify(_core(f"/decisions/{i}/approve", i.encode(), who="dashboard")), ui.navigate.reload())).props("color=green")
                        ui.button("Deny", on_click=lambda _, i=did: (ui.notify(_core(f"/decisions/{i}/deny", i.encode(), who="dashboard")), ui.navigate.reload())).props("color=red outline").classes("ml-6")
        with ui.expansion("Linimasa", icon="timeline").classes("w-full"):
            for t in inc.timeline:
                ui.label(f"{_t(t['ts'])} · {t['from']} → {t['to']} · {t.get('note','')} · {t.get('actor','')}").classes("text-xs")


@ui.page("/budget")
def budget():
    from warden.steward import ledger
    header("Anggaran & ETTR")
    p = ledger.projection()
    with ui.row().classes("px-3 gap-3 flex-wrap"):
        for k, v in (("hari ini", f"${p['today_usd']:.2f}"), ("bulan berjalan", f"${p['month_to_date_usd']:.2f}"), ("bakar/jam", f"${p['burn_usd_per_hour']:.3f}"),
                     ("runway", f"{p['runway_days']} hari" if p["runway_days"] else "∞"), ("kalau dibiarkan 30 hari", f"${p['if_left_running_30d_usd']:.0f}")):
            with ui.card().classes("p-3 min-w-36"):
                ui.label(k).classes("text-xs text-grey"); ui.label(v).classes("text-lg font-semibold")
    ui.linear_progress(min(1.0, p["month_to_date_usd"] / p["cap_usd"])).classes("px-3")
    ui.label(f"pagu ${p['cap_usd']:.0f} · ambang 50/80/100 % = peringatan / stop demo / stop semua").classes("text-xs text-grey px-3")
    with ui.column().classes("w-full px-3 gap-2"):
        for j in db.jobs.list(limit=100):
            e = ledger.ettr(j.job_id)
            with ui.card().classes("w-full p-3"):
                ui.label(f"{j.job_id} · {j.status} · fase {j.phase} · ${j.spent_usd:.3f}").classes("font-semibold")
                ui.label(f"ETTR {e.get('ettr') if e.get('ettr') is not None else '—'} · efektif {e.get('effective_h','—')} jam / dibayar {e.get('paid_h','—')} jam").classes("text-sm")
    ui.button("Sapu sekarang", on_click=lambda: ui.notify(_core("/steward"))).props("outline").classes("m-3")


@ui.page("/policies")
def policies():
    from warden.policy.engine import load_policy
    header("Kebijakan")
    pol = load_policy()
    with ui.column().classes("w-full px-3 gap-2"):
        with ui.card().classes("w-full p-3"):
            ui.label("Tingkat otonomi per tindakan (L0 amati · L1 minta izin · L2 lakukan lalu lapor · L3 diam)").classes("font-semibold")
            for a, l in pol["autonomy"].items():
                ui.label(f"{a}: {l}  {json.dumps(pol['limits'].get(a, {}))}").classes("text-sm")
        with ui.card().classes("w-full p-3"):
            ui.label("Pagu global & circuit breaker").classes("font-semibold")
            ui.code(json.dumps({"global": pol["global"], "circuit_breaker": pol["circuit_breaker"], "hard_deny": pol["hard_deny"]}, indent=1)).classes("w-full text-xs")
        ov = [d.to_dict() | {"id": d.id} for d in db.client().collection("policy_overrides").stream()]
        with ui.card().classes("w-full p-3"):
            ui.label("Override aktif (Always 24h)").classes("font-semibold")
            if not ov:
                ui.label("tidak ada").classes("text-grey text-sm")
            for o in ov:
                ui.label(f"{o['id']} → {o.get('level')} oleh {o.get('by')}").classes("text-sm")


@ui.page("/audit")
def audit():
    header("Audit (hanya-tambah)")
    rows = [d.to_dict() for d in db.client().collection("audit").order_by("ts", direction="DESCENDING").limit(200).stream()]
    if not rows:
        _empty("Belum ada tindakan.")
    with ui.column().classes("w-full px-3 gap-1"):
        for r in rows:
            ui.label(f"{_t(r.get('ts'))} · {r.get('actor')} · {r.get('phase')} · {r.get('action')} → {r.get('target')} · {'✅' if r.get('ok') else ('❌ ' + str(r.get('error',''))[:80] if r.get('ok') is False else '')}").classes("text-xs")


@ui.page("/health")
def health():
    header("Kesehatan Warden")
    rows = [d.to_dict() | {"src": d.id} for d in db.client().collection("health").stream()]
    with ui.column().classes("w-full px-3 gap-2"):
        for r in sorted(rows, key=lambda x: x["src"]):
            last = r.get("last_ok_at"); age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() if last else None
            stale = age is None or age > 900
            with ui.card().classes("w-full p-3"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(r["src"]).classes("font-semibold")
                    ui.badge("basi" if stale else "hidup", color="red" if stale else "green")
                ui.label(f"terakhir OK {_t(last)} · gagal berturut {r.get('consecutive_failures', 0)} · {r.get('last_error','')[:120]}").classes("text-xs text-grey")
                if r.get("stats"):
                    ui.label(json.dumps(r["stats"])).classes("text-xs")


def run():
    ui.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), title="Warden", reload=False, show=False, dark=None, storage_secret=os.environ.get("WARDEN_UI_SECRET", "dev"))


if __name__ in {"__main__", "__mp_main__"}:
    run()
