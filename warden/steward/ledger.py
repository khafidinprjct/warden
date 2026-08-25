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


def apply_promotions(policy: dict | None = None, notify=None) -> dict[str, Any]:
    """Graduated trust (checklist G2): an L1 action approved by a human `streak` times in a row for a job, with no failure or rejection,
    is promoted to L2 for that job; a failed verification of an L2 action demotes it back to L1. Both are audited and reversible."""
    from warden.core.models import AuditEntry
    from warden.policy.engine import load_policy
    pol = (policy or load_policy()).get("promotion", {})
    out = {"promoted": [], "demoted": []}
    if not pol.get("auto", False):
        return out
    for c in promotion_candidates(min_streak=int(pol.get("streak", 5))):
        job = db.jobs.get(c["job_id"])
        if not job or job.autonomy_overrides.get(c["action"]) == "L2":
            continue
        job.autonomy_overrides[c["action"]] = "L2"; db.jobs.put(job)
        db.audit(AuditEntry(actor="warden", phase="result", action="promote", target=c["job_id"], before={"level": "L1", "streak": c["streak"]}, after={"level": "L2", "action": c["action"]}, ok=True))
        out["promoted"].append(c)
        if notify: notify(None, None, f"⬆️ {c['job_id']}: {c['action']} promoted L1 → L2 after {c['streak']} consecutive approvals without a failure")
    if pol.get("demote_on_failed_verification", True):
        for inc in db.incidents.list(limit=300):
            if (inc.verify or {}).get("result") != "fail" or (now() - inc.updated_at) > timedelta(hours=24):
                continue
            for did in inc.decision_ids:
                d = db.decisions.get(did)
                if not d or d.verdict != "AUTO" or d.action == "notify":
                    continue
                job = db.jobs.get(d.job_id) if d.job_id else None
                if job and job.autonomy_overrides.get(d.action.value) == "L2":
                    job.autonomy_overrides[d.action.value] = "L1"; db.jobs.put(job)
                    db.audit(AuditEntry(actor="warden", phase="result", action="demote", target=d.job_id, before={"level": "L2"}, after={"level": "L1", "action": d.action.value, "incident": inc.incident_id}, ok=True))
                    out["demoted"].append({"job_id": d.job_id, "action": d.action.value, "incident": inc.incident_id})
                    if notify: notify(inc, None, f"⬇️ {d.job_id}: {d.action.value} demoted L2 → L1 — its result did not verify ({inc.incident_id})")
    return out


def hold(job_id: str, minutes: int, who: str) -> dict[str, Any]:
    """Manual mode (G4): the operator takes the machine; Warden observes only until the hold expires."""
    from warden.core.models import AuditEntry
    job = db.jobs.get(job_id)
    if not job:
        return {"ok": False, "error": "job not found"}
    job.operator_hold_until = now() + timedelta(minutes=minutes) if minutes > 0 else None; db.jobs.put(job)
    db.audit(AuditEntry(actor=f"human:{who}", phase="result", action="hold", target=job_id, after={"until": job.operator_hold_until.isoformat() if job.operator_hold_until else None}, ok=True))
    return {"ok": True, "job_id": job_id, "hold_until": job.operator_hold_until.isoformat() if job.operator_hold_until else None}


def learn_baselines() -> dict[str, Any]:
    """Per-job expectations from verified data (F4/B5): checkpoint size from VERIFIED markers, step rate and heartbeat interval from
    healthy heartbeats. Written to job.expect only where the operator did not set a value; stored under baselines/<job> in full."""
    import statistics
    out: dict[str, Any] = {}
    for job in db.jobs.list(limit=200):
        if job.status not in (JobStatus.RUNNING, JobStatus.COMPLETE, JobStatus.FINISHED_UNVERIFIED):
            continue
        b: dict[str, Any] = {}
        hbs = [h for h in db.recent_heartbeats(job.job_id, 300) if h.step_per_s]
        if len(hbs) >= 10:
            b["step_per_s_median"] = round(statistics.median(h.step_per_s for h in hbs), 4)
            gaps = sorted((y.ts - x.ts).total_seconds() for x, y in zip(hbs, hbs[1:]))
            b["heartbeat_interval_p95_s"] = round(gaps[int(0.95 * (len(gaps) - 1))], 1)
        ver = db.get_marker(job.job_id, job.run_id, "VERIFIED") if job.run_id else None
        if ver:
            sizes = [a.get("bytes", 0) for a in ver.artifacts if str(a.get("name", "")).startswith("ckpt") or str(a.get("name", "")).endswith((".pt", ".pth", ".ckpt", ".npz"))]
            if sizes:
                b["ckpt_size_bytes"] = int(statistics.median(sizes))
        if not b:
            continue
        b["updated_at"] = now().isoformat(); b["source"] = "learned"
        db.client().collection("baselines").document(job.job_id).set(b)
        changed = False
        for k in ("ckpt_size_bytes",):
            if k in b and not job.expect.get(k):
                job.expect[k] = b[k]; changed = True
        if "step_per_s_median" in b and not job.expect.get("baseline_step_per_s_user"):
            job.expect["baseline_step_per_s"] = b["step_per_s_median"]; changed = True
        if changed:
            db.jobs.put(job)
        out[job.job_id] = b
    return out
