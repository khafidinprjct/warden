"""Pipeline insiden LLM (Fase 4): insiden DIAGNOSING → bukti → Diagnostician (Gemini, JSON) → cek silang →
(opsional vonis kedua) → kebijakan → eksekusi/izin. LLM tidak memegang tombol (P1)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from warden.agents.crosscheck import crosscheck
from warden.agents.diagnostician import diagnose
from warden.agents.schemas import Diagnosis, Recommended
from warden.config import settings
from warden.core.models import Action, DecisionStatus, Evidence, IncidentState as S, Verdict, now
from warden.core.state_machine import transition
from warden.executor import registry as ex
from warden.policy.engine import load_policy
from warden.providers.registry import compute
from warden.store import firestore as db
from warden.watcher.tick import _ctx_for, _is_frozen

POLICY = load_policy()
REC2ACT = {Recommended.resume_same: Action.RESUME_JOB, Recommended.resume_smaller_batch: Action.RESUME_JOB,
           Recommended.restart_clean: Action.RESUME_JOB, Recommended.stop: Action.STOP_INSTANCE,
           Recommended.escalate: Action.NOTIFY, Recommended.patch_suggest: Action.NOTIFY, Recommended.noop: Action.NOTIFY}


def read_log_tail(job_id: str, n: int = 200) -> list[str]:
    """Log dari GCS (jalur pasif) bila bucket ada; lokal data/gcs/ untuk pengembangan."""
    if settings.bucket:
        try:
            from google.cloud import storage
            b = storage.Client().bucket(settings.bucket)
            blob = b.blob(f"jobs/{job_id}/log/tail.log")
            if blob.exists():
                return blob.download_as_text(errors="ignore").splitlines()[-n:]
        except Exception as e:
            db.health("gcs", False, str(e)[:200])
    p = Path("data/gcs") / job_id / "tail.log"
    return p.read_text(errors="ignore").splitlines()[-n:] if p.exists() else []


def _job_card(inc, job, inst) -> dict:
    hist = [{"rule": i.rule, "state": str(i.state), "diag": (i.diagnosis or {}).get("category")}
            for i in db.incidents.list(job_id=inc.job_id, limit=50)][-5:]
    return {"job": inc.job_id, "phase": job.phase if job else "", "last_step": job.last_step if job else None,
            "instance": inc.instance_ref, "hourly_usd": inst.hourly_price_usd if inst else None,
            "burning_usd_per_hour": inc.cost_burning_usd_per_hour, "recent_incidents": hist, "legacy": bool(job and job.legacy)}


def _hb_summary(job_id: str) -> dict:
    hbs = db.recent_heartbeats(job_id, 10)
    if not hbs:
        return {}
    last = hbs[-1]
    rates = [h.step_per_s for h in hbs if h.step_per_s]
    return {"last": {k: getattr(last, k) for k in ("phase", "step", "loss", "grad_norm", "step_per_s", "vram_used_mb", "vram_total_mb", "gpu_util", "cpu_pct", "disk_avail_gb")},
            "baseline_step_per_s": (sorted(rates)[len(rates) // 2] if rates else None),
            "losses": [h.loss for h in hbs][-10:], "synthetic": last.synthetic}


def process_diagnosing(notify: Callable | None = None, max_n: int = 5) -> dict[str, Any]:
    stats = {"processed": 0, "auto": 0, "approval": 0, "escalated": 0, "llm_usd": 0.0}
    today = db.cost_today()
    if float(today.get("llm_usd", 0.0)) >= settings.llm_daily_cap_usd:
        db.health("llm_budget", False, "pagu LLM harian tercapai — deterministik saja")
        return stats
    frozen = _is_frozen()
    for inc in db.incidents.list(state="DIAGNOSING", limit=max_n):
        job = db.jobs.get(inc.job_id) if inc.job_id else None
        inst = compute().describe(inc.instance_ref) if inc.instance_ref else None
        lines = read_log_tail(inc.job_id)
        findings = [{"rule": inc.rule, "summary": inc.summary}] + [db.evidence.get(e).payload for e in inc.evidence_ids if db.evidence.get(e)]
        hbsum = _hb_summary(inc.job_id)
        try:
            diag, usage = diagnose(_job_card(inc, job, inst), findings, hbsum, lines or ["(log tidak tersedia)"])
        except Exception as e:
            db.health("gemini", False, str(e)[:200])
            transition(inc, S.ESCALATED, note=f"Gemini gagal: {e}"[:200]); db.incidents.put(inc)
            if notify: notify(inc, None, f"⚠️ {inc.summary} — diagnosis LLM gagal: {str(e)[:120]}")
            continue
        db.health("gemini", True)
        cc = crosscheck(diag, lines, hbsum.get("last") | {"baseline_step_per_s": hbsum.get("baseline_step_per_s")} if hbsum.get("last") else None)
        conf = cc["adjusted_confidence"]
        # vonis kedua bila ragu atau dampak luas
        if (conf < 0.7 or diag.blast_radius in ("this_job", "budget", "artifacts")) and cc["passed"]:
            try:
                diag2, usage2 = diagnose(_job_card(inc, job, inst), findings, hbsum, lines or ["-"], model=settings.gemini_model_second)
                usage["cost_usd"] += usage2["cost_usd"]
                cc["second_opinion"] = {"model": usage2["model"], "category": diag2.category, "action": diag2.recommended_action}
                if diag2.category != diag.category or diag2.recommended_action != diag.recommended_action:
                    cc["needs_human"] = True; cc["checks"].append({"check": "second_opinion_agrees", "ok": False, "note": f"{diag2.category}/{diag2.recommended_action}"})
                else:
                    cc["checks"].append({"check": "second_opinion_agrees", "ok": True})
            except Exception as e:
                cc["checks"].append({"check": "second_opinion", "ok": False, "note": str(e)[:100]})
        inc.diagnosis = diag.model_dump(mode="json"); inc.crosscheck = cc; inc.llm_cost_usd += usage["cost_usd"]
        stats["llm_usd"] += usage["cost_usd"]; db.cost_add(now().strftime("%Y-%m-%d"), "llm_usd", usage["cost_usd"], inc.job_id)
        ev = Evidence(incident_id=inc.incident_id, kind="log_window", summary=f"{len(lines)} baris log", payload={"lines": diag.evidence_lines, "quotes": diag.evidence_quotes})
        db.evidence.put(ev); inc.evidence_ids.append(ev.evidence_id)
        transition(inc, S.DIAGNOSED, note=f"{diag.category} conf={conf:.2f} cc={'ok' if cc['passed'] else 'GAGAL'}")
        action = REC2ACT.get(diag.recommended_action, Action.NOTIFY)
        if cc["needs_human"] and action != Action.NOTIFY:
            forced_l1 = True
        else:
            forced_l1 = False
        ctx = _ctx_for(job, inst, action, frozen); ctx.llm_confidence = conf
        from warden.policy.engine import evaluate as policy_eval
        dec = policy_eval(action, ctx, POLICY)
        if forced_l1 and dec.verdict == Verdict.AUTO:
            dec.verdict = Verdict.NEED_APPROVAL; dec.explain.append("cek silang/vonis kedua meminta manusia → L1")
            from datetime import timedelta
            dec.expires_at = now() + timedelta(minutes=POLICY["global"]["approval_ttl_minutes"])
        dec.incident_id = inc.incident_id
        dec.params = {"instance_ref": inc.instance_ref, "run_id": job.run_id if job else "", **diag.action_params}
        if action != Action.NOTIFY:
            dec.dry_run_plan = ex.dry_run(dec, compute())
        db.decisions.put(dec); inc.decision_ids.append(dec.decision_id)
        transition(inc, S.DECIDED, note=f"{action}: {dec.verdict}")
        text = f"🧠 {diag.human_summary_id} | {diag.category} conf {conf:.2f} → {action} ({dec.verdict})"
        if dec.verdict == Verdict.AUTO:
            transition(inc, S.EXECUTING); dec.status = DecisionStatus.EXECUTING; db.decisions.put(dec)
            r = ex.execute(dec, compute()); dec.status = DecisionStatus.DONE if r.ok else DecisionStatus.FAILED
            transition(inc, S.VERIFYING if r.ok else S.FAILED_ACTION, note=r.observed or r.error)
            transition(inc, S.RESOLVED if r.ok else S.ESCALATED, note="diminta-vs-jadi" if r.ok else r.error)
            stats["auto"] += 1; text += f" → {'✅ ' + r.observed if r.ok else '❌ ' + r.error}"
        elif dec.verdict == Verdict.NEED_APPROVAL:
            transition(inc, S.AWAITING_APPROVAL); stats["approval"] += 1
        elif dec.verdict == Verdict.HELD:
            transition(inc, S.HELD)
        else:
            transition(inc, S.ESCALATED, note="ditolak kebijakan"); stats["escalated"] += 1
        db.decisions.put(dec); db.incidents.put(inc); stats["processed"] += 1
        if notify: notify(inc, dec, text)
    return stats
