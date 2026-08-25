"""Steward (Fase 6): buku besar biaya real-time, ETTR (R6), proyeksi runway, kill-switch anggaran, digest."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from warden.config import settings
from warden.core.models import InstanceStatus, JobStatus, now
from warden.providers.registry import compute
from warden.store import firestore as db


def accrue(tick_seconds: int | None = None) -> dict[str, Any]:
    """Tambahkan biaya mesin RUNNING selama satu interval tick ke ledger (sinyal cepat; Billing resmi = jaring akhir)."""
    dt_h = (tick_seconds or settings.tick_seconds) / 3600.0
    day = now().strftime("%Y-%m-%d")
    total = 0.0; n = 0
    for inst in compute().list_instances():
        if inst.managed and inst.status == InstanceStatus.RUNNING:
            usd = inst.hourly_price_usd * dt_h
            db.cost_add(day, "compute_usd", usd, inst.ref); total += usd; n += 1
            if inst.job_id:
                job = db.jobs.get(inst.job_id)
                if job:
                    job.spent_usd = round(job.spent_usd + usd, 6); db.jobs.put(job)
    return {"running": n, "usd_added": round(total, 6)}


def ettr(job_id: str, window_hours: float = 24.0) -> dict[str, Any]:
    """Effective Training Time Ratio = waktu training efektif ÷ waktu mesin dibayar (R6).
    Efektif = interval antar-denyut yang step-nya bertambah; dibayar = umur mesin RUNNING dari ledger denyut."""
    hbs = db.recent_heartbeats(job_id, 500)
    if len(hbs) < 2:
        return {"job_id": job_id, "ettr": None, "note": "heartbeats < 2"}
    t0 = now() - timedelta(hours=window_hours)
    hbs = [h for h in hbs if h.ts >= t0]
    eff = paid = 0.0
    for a, b in zip(hbs, hbs[1:]):
        gap = (b.ts - a.ts).total_seconds()
        if gap > 600:           # jeda > 10 mnt = mesin mati/preempt: tidak dihitung dibayar
            continue
        paid += gap
        if (b.step or 0) > (a.step or 0) or (a.step is None and b.loss is not None):
            eff += gap
    return {"job_id": job_id, "ettr": round(eff / paid, 3) if paid else None, "effective_h": round(eff / 3600, 2), "paid_h": round(paid / 3600, 2)}


def projection() -> dict[str, Any]:
    today = db.cost_today()
    spent = float(today.get("compute_usd", 0.0)) + float(today.get("llm_usd", 0.0))
    burn = sum(i.hourly_price_usd for i in compute().list_instances() if i.managed and i.status == InstanceStatus.RUNNING)
    cap = float(db.client().collection("policies").document("runtime").get().to_dict().get("budget_total_usd", 150.0)) \
        if db.client().collection("policies").document("runtime").get().exists else 150.0
    mtd = 0.0
    for d in db.client().collection("costs").stream():
        x = d.to_dict(); mtd += float(x.get("compute_usd", 0.0)) + float(x.get("llm_usd", 0.0))
    return {"today_usd": round(spent, 4), "burn_usd_per_hour": round(burn, 4), "month_to_date_usd": round(mtd, 4),
            "runway_days": (round((cap - mtd) / (burn * 24), 1) if burn > 0 else None), "cap_usd": cap,
            "if_left_running_30d_usd": round(burn * 24 * 30, 2)}


def budget_kill_switch(pct: float, notify=None) -> dict[str, Any]:
    """Ambang Billing Budget: 0,5 peringatan; 0,8 stop mesin demo + turunkan model; 1,0 stop semua + baca-saja."""
    rt = db.client().collection("policies").document("runtime")
    acted: list[str] = []
    if pct >= 1.0:
        for inst in compute().list_instances():
            if inst.managed and inst.status == InstanceStatus.RUNNING:
                r = compute().stop(inst.ref); acted.append(f"stop {inst.ref}: {r.observed or r.error}")
        rt.set({"read_only": True, "budget_pct": pct}, merge=True)
    elif pct >= 0.8:
        for inst in compute().list_instances():
            if inst.managed and inst.status == InstanceStatus.RUNNING and inst.labels.get("warden-role") == "demo":
                r = compute().stop(inst.ref); acted.append(f"stop demo {inst.ref}: {r.observed or r.error}")
        rt.set({"llm_lite_only": True, "budget_pct": pct}, merge=True)
    else:
        rt.set({"budget_pct": pct}, merge=True)
    if notify:
        notify(None, None, f"💸 Budget {pct*100:.0f}% — actions: {acted or 'warning only'} | projection: {projection()}")
    return {"pct": pct, "acted": acted}


def digest() -> str:
    p = projection(); jobs = db.jobs.list(limit=100)
    lines = [f"📋 Warden digest {now():%Y-%m-%d %H:%M} UTC",
             f"today ${p['today_usd']:.2f} · month-to-date ${p['month_to_date_usd']:.2f} · burn ${p['burn_usd_per_hour']:.3f}/h · runway {p['runway_days']} days"]
    for j in jobs:
        if j.status in (JobStatus.RUNNING, JobStatus.FINISHED_UNVERIFIED):
            e = ettr(j.job_id)
            lines.append(f"• {j.job_id} {j.status} phase {j.phase} step {j.last_step} ETTR {e.get('ettr')} (effective {e.get('effective_h')}h / paid {e.get('paid_h')}h) ${j.spent_usd:.2f}")
    inc = [i for i in db.incidents.list(limit=200) if i.created_at > now() - timedelta(days=1)]
    lines.append(f"incidents 24h: {len(inc)} (RESOLVED {sum(1 for i in inc if str(i.state)=='RESOLVED')}, awaiting approval {sum(1 for i in inc if str(i.state)=='AWAITING_APPROVAL')})")
    return "\n".join(lines)


def expire_overrides() -> int:
    """Override 'Always 24h' yang lewat masa berlakunya dicabut (job.autonomy_overrides dikembalikan)."""
    import time
    n = 0
    for d in db.client().collection("policy_overrides").stream():
        o = d.to_dict()
        if float(o.get("until", 0)) < time.time():
            job_id, action = d.id.split(":", 1)
            job = db.jobs.get(job_id)
            if job and action in job.autonomy_overrides:
                job.autonomy_overrides.pop(action); db.jobs.put(job)
            d.reference.delete(); n += 1
    return n


def promotion_candidates(min_streak: int = 10) -> list[dict[str, Any]]:
    """Tindakan L1 yang disetujui manusia ≥ min_streak kali berturut tanpa kegagalan → USUL naik ke L2 (keputusan manusia)."""
    from collections import defaultdict
    streak: dict[tuple[str, str], int] = defaultdict(int); broken: set[tuple[str, str]] = set()
    decs = sorted([d for d in db.decisions.list(limit=1000) if d.verdict == "NEED_APPROVAL"], key=lambda d: d.created_at, reverse=True)
    for d in decs:
        k = (d.job_id, d.action.value)
        if k in broken:
            continue
        if d.status == "DONE" and d.approved_by:
            streak[k] += 1
        elif d.status in ("FAILED", "REJECTED"):
            broken.add(k)
    return [{"job_id": j, "action": a, "streak": n, "usul": "L1 → L2"} for (j, a), n in streak.items() if n >= min_streak]
