"""Aturan deterministik Watcher (rencana §5.1). Semua dua-syarat bila menyangkut 'diam'.
Input = potret fakta (Facts) yang disiapkan tick; output = daftar Finding. Tanpa I/O, tanpa LLM."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from warden.core.models import Heartbeat, Instance, InstanceStatus, Job, JobStatus, Marker


@dataclass
class Facts:
    t: datetime
    inst: Instance | None
    job: Job | None
    hb: Heartbeat | None                       # denyut terakhir
    hbs: list[Heartbeat] = field(default_factory=list)   # 30 terakhir (untuk baseline)
    run_fin: Marker | None = None
    done_legacy: Marker | None = None
    preempt_events: list[dict] = field(default_factory=list)
    prev_status: InstanceStatus | None = None  # status pada tick sebelumnya (dua tick berturut)
    in_ledger: bool = True
    boot_age_min: float = 999.0
    policy: dict = field(default_factory=dict)


@dataclass
class Finding:
    rule: str
    severity: str
    summary: str
    dedupe_key: str
    needs_llm: bool = False
    suggested_action: str = ""
    evidence: dict = field(default_factory=dict)


def _stale_threshold(hbs: list[Heartbeat], phase: str) -> timedelta:
    """T_stale = clamp(3 × p95 interval, 5 mnt, 45 mnt) dari denyut sehat sebelumnya."""
    ts = sorted(h.ts for h in hbs if h.phase == phase or not phase)
    if len(ts) < 5:
        return timedelta(minutes=5)
    gaps = sorted((b - a).total_seconds() for a, b in zip(ts, ts[1:]))
    p95 = gaps[int(0.95 * (len(gaps) - 1))]
    return timedelta(seconds=min(max(3 * p95, 300), 2700))


def evaluate(f: Facts) -> list[Finding]:
    out: list[Finding] = []
    inst, job, hb = f.inst, f.job, f.hb
    g = f.policy.get("global", {"idle_grace_minutes": 15, "orphan_grace_minutes": 10, "boot_grace_minutes": 10})

    # R2 konfigurasi tak aman (sebelum apa pun)
    if inst and inst.managed and (inst.boot_disk_auto_delete is True or inst.termination_action == "DELETE"):
        out.append(Finding("unsafe_config", "critical", f"{inst.ref}: auto-delete disk / termination DELETE",
                           f"unsafe:{inst.ref}", suggested_action="notify",
                           evidence={"boot_disk_auto_delete": inst.boot_disk_auto_delete, "termination_action": inst.termination_action}))

    # R1 mesin mati (dua tick berturut) DAN tanpa RUN_FIN → preempt/dihentikan luar
    if inst and job and job.status == JobStatus.RUNNING and inst.status == InstanceStatus.TERMINATED \
            and f.prev_status == InstanceStatus.TERMINATED and f.run_fin is None:
        preempted = any(e.get("type", "").endswith("preempted") for e in f.preempt_events)
        out.append(Finding("preempted" if preempted else "stopped_external", "critical",
                           f"{inst.ref} TERMINATED without RUN_FIN ({'preempted' if preempted else 'stopped externally'}); job {job.job_id} phase {job.phase}",
                           f"down:{inst.ref}:{inst.boot_id}", suggested_action="start_instance",
                           evidence={"preempt_events": f.preempt_events[-3:], "phase": job.phase, "last_step": job.last_step}))

    # R3 marker
    if f.done_legacy and f.run_fin is None:
        out.append(Finding("done_without_exit", "warning", f"{job.job_id if job else '?'}: DONE marker without RUN_FIN/exit code — NOT accepted",
                           f"donenoexit:{f.done_legacy.job_id}:{f.done_legacy.run_id}", suggested_action="notify"))
    if f.run_fin and not f.run_fin.valid:
        out.append(Finding("marker_invalid", "warning", f"RUN_FIN invalid: {f.run_fin.invalid_reason}",
                           f"mkinvalid:{f.run_fin.job_id}:{f.run_fin.run_id}", suggested_action="notify"))
    if f.run_fin and f.run_fin.valid and f.run_fin.exit_code not in (None, 0):
        out.append(Finding("run_fin_nonzero", "critical", f"job {f.run_fin.job_id} ended with exit={f.run_fin.exit_code}",
                           f"exit:{f.run_fin.job_id}:{f.run_fin.run_id}", needs_llm=True,
                           evidence={"exit_code": f.run_fin.exit_code, "signal": f.run_fin.signal}))
    if f.run_fin and f.run_fin.valid and f.run_fin.exit_code == 0 and job and job.status != JobStatus.COMPLETE:
        out.append(Finding("fin_ok_pending_verify", "info", f"job {f.run_fin.job_id} exit 0 — awaiting artifact verification",
                           f"verify:{f.run_fin.job_id}:{f.run_fin.run_id}", suggested_action="verify",
                           evidence={"artifacts": f.run_fin.artifacts}))

    # R6 macet dua-syarat: denyut basi DAN (gpu < 5% ATAU cpu < 10%)
    if inst and inst.status == InstanceStatus.RUNNING and job and job.status == JobStatus.RUNNING and hb and f.run_fin is None:
        age = f.t - hb.ts
        thr = _stale_threshold(f.hbs, hb.phase)
        quiet = (hb.gpu_util is not None and hb.gpu_util < 5) or (hb.cpu_pct is not None and hb.cpu_pct < 10)
        if age > thr and quiet:
            out.append(Finding("stuck", "critical", f"{job.job_id}: heartbeat stale {age.total_seconds()/60:.0f} min (> {thr.total_seconds()/60:.0f}) AND machine idle",
                               f"stuck:{job.job_id}:{hb.run_id}", needs_llm=True, suggested_action="resume_job",
                               evidence={"age_s": age.total_seconds(), "thr_s": thr.total_seconds(), "gpu": hb.gpu_util, "cpu": hb.cpu_pct}))
        elif age > thr:
            out.append(Finding("slow", "warning", f"{job.job_id}: heartbeat stale but machine busy (slow, not stuck)",
                               f"slow:{job.job_id}:{hb.run_id}", evidence={"age_s": age.total_seconds()}))
        # R6b denyut harness mati total (> 3 mnt tanpa denyut host saat RUNNING)
        if age > timedelta(minutes=3) and hb.synthetic is False and not hb.procs and hb.cpu_pct is None:
            out.append(Finding("harness_dead", "critical", f"{job.job_id}: no harness heartbeat for {age.total_seconds()/60:.0f} min while machine RUNNING",
                               f"harnessdead:{job.job_id}:{inst.boot_id}"))

    # R8 disk
    if hb and hb.disk_avail_gb is not None:
        need = float((job.expect or {}).get("ckpt_size_bytes", 0)) / 1e9 if job else 0.0
        if hb.disk_avail_gb < max(2 * need, 5.0):
            sev = "critical" if hb.disk_avail_gb < max(need, 1.0) else "warning"
            out.append(Finding("disk_low", sev, f"{hb.job_id}: disk free {hb.disk_avail_gb:.1f} GB (need ≥ {max(2*need,5.0):.1f})",
                               f"disk:{hb.job_id}:{sev}", suggested_action="notify", evidence={"avail_gb": hb.disk_avail_gb}))

    # R7 proses ganda (entrypoint path penuh, worker dikecualikan via ppid)
    if hb and hb.procs and job and job.command:
        entry = job.command.split()[0] if job.command else ""
        mains = [p for p in hb.procs if entry and entry in p.get("cmd", "") and p.get("ppid") in (1, None) or p.get("ppid") == 1]
        if len(mains) > 1:
            out.append(Finding("dup_process", "critical", f"{job.job_id}: {len(mains)} entrypoint processes running concurrently",
                               f"dup:{job.job_id}:{hb.run_id}", suggested_action="kill_process", evidence={"pids": [p.get("pid") for p in mains]}))

    # R5 yatim & idle (dua-syarat, grace)
    if inst and inst.status == InstanceStatus.RUNNING and f.boot_age_min > g["boot_grace_minutes"]:
        quiet = hb is None or ((hb.gpu_util or 0) < 5 and (hb.cpu_pct or 0) < 10)
        if not f.in_ledger or (job is None) or job.status in (JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.ABANDONED):
            if quiet and f.boot_age_min > g["orphan_grace_minutes"]:
                out.append(Finding("orphan", "warning", f"{inst.ref} running at ${inst.hourly_price_usd:.3f}/h with no active job",
                                   f"orphan:{inst.ref}:{inst.boot_id}", suggested_action="stop_instance",
                                   evidence={"hourly": inst.hourly_price_usd, "in_ledger": f.in_ledger}))
        elif job and job.status == JobStatus.RUNNING and hb and quiet and (f.t - hb.ts) > timedelta(minutes=g["idle_grace_minutes"]):
            out.append(Finding("idle", "warning", f"{inst.ref}: job {job.job_id} idle ≥ {g['idle_grace_minutes']} min",
                               f"idle:{inst.ref}:{inst.boot_id}", suggested_action="stop_instance"))

    # R-loss: NaN/divergen dari denyut (tanpa LLM)
    if hb and hb.loss is not None and job and job.status == JobStatus.RUNNING:
        import math
        if math.isnan(hb.loss) or math.isinf(hb.loss):
            out.append(Finding("nan_loss", "critical", f"{job.job_id}: non-finite loss at step {hb.step}",
                               f"nan:{job.job_id}:{hb.run_id}", needs_llm=True, suggested_action="notify"))
    return out
