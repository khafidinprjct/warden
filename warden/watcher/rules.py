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
    preflight_fail: Marker | None = None
    smoke_fin: Marker | None = None
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
    action_params: dict = field(default_factory=dict)


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
        recent = 0
        for e in f.preempt_events:
            try:
                ts = datetime.fromisoformat(str(e.get("ts", "")).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=f.t.tzinfo)
                recent += (f.t - ts) <= timedelta(minutes=60)
            except ValueError:
                pass
        storm = preempted and recent >= 3          # B4: a zone that preempts 3× in an hour is not worth another start
        out.append(Finding("preempt_storm" if storm else "preempted" if preempted else "stopped_external", "critical",
                           f"{inst.ref} TERMINATED without RUN_FIN ({f'preempted {recent}× in 60 min — storm' if storm else 'preempted' if preempted else 'stopped externally'}); job {job.job_id} phase {job.phase}",
                           f"down:{inst.ref}:{inst.boot_id}", suggested_action="relocate_zone" if storm else "start_instance",
                           evidence={"preempt_events": f.preempt_events[-3:], "preempts_last_hour": recent, "phase": job.phase, "last_step": job.last_step}))

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

    # A6 close-out: job COMPLETE (artifacts VERIFIED) but the machine still runs → stop it now, do not wait for idle grace
    if inst and inst.status == InstanceStatus.RUNNING and job and job.status == JobStatus.COMPLETE and f.boot_age_min > 2:
        out.append(Finding("complete_running", "info", f"{job.job_id} is COMPLETE (verified) — stopping {inst.ref} (${inst.hourly_price_usd:.3f}/h)",
                           f"complete:{inst.ref}:{job.run_id}", suggested_action="stop_instance", evidence={"hourly": inst.hourly_price_usd}))
    # A3 preflight failed on the machine → nothing expensive should start; stop the spend and tell the human what is missing
    if f.preflight_fail and job and job.status in (JobStatus.PENDING, JobStatus.RUNNING) and inst and inst.status == InstanceStatus.RUNNING:
        out.append(Finding("preflight_fail", "critical", f"{job.job_id}: preflight failed on {inst.ref}: {f.preflight_fail.evidence.get('reason', '?')}",
                           f"preflight:{job.job_id}:{inst.boot_id}", suggested_action="stop_instance", evidence=dict(f.preflight_fail.evidence)))
    # A7 smoke that did not load the declared components is not a smoke
    if f.smoke_fin and job:
        want = set((job.expect or {}).get("smoke_members", [])); got = set(f.smoke_fin.evidence.get("members", []))
        if want and not want <= got:
            out.append(Finding("smoke_invalid", "critical", f"{job.job_id}: smoke missing members {sorted(want - got)} — not accepted",
                               f"smoke:{job.job_id}:{f.smoke_fin.run_id}", suggested_action="notify", evidence={"want": sorted(want), "got": sorted(got)}))
    # J3 job budget: 80 % warn, 100 % stop
    if job and job.budget_cap_usd > 0 and job.status == JobStatus.RUNNING and inst and inst.status == InstanceStatus.RUNNING:
        pct = job.spent_usd / job.budget_cap_usd
        if pct >= 1.0:
            out.append(Finding("budget_exhausted", "critical", f"{job.job_id}: spent ${job.spent_usd:.2f} ≥ budget ${job.budget_cap_usd:.2f} — stopping",
                               f"budget100:{job.job_id}", suggested_action="stop_instance", evidence={"spent": job.spent_usd, "cap": job.budget_cap_usd}))
        elif pct >= 0.8:
            out.append(Finding("budget_80", "warning", f"{job.job_id}: spent ${job.spent_usd:.2f} = {pct*100:.0f}% of budget ${job.budget_cap_usd:.2f}",
                               f"budget80:{job.job_id}", suggested_action="notify", evidence={"spent": job.spent_usd, "cap": job.budget_cap_usd}))

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
                               f"disk:{hb.job_id}:{sev}", suggested_action="clean_disk", evidence={"avail_gb": hb.disk_avail_gb},
                               action_params={"keep": 2, "min_free_gb": max(2 * need, 5.0)}))

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
        if not f.in_ledger or (job is None) or job.status in (JobStatus.FAILED, JobStatus.ABANDONED) \
                or (job.status == JobStatus.COMPLETE and not any(o.rule == "complete_running" for o in out)):   # COMPLETE is handled by close-out
            if quiet and f.boot_age_min > g["orphan_grace_minutes"]:
                out.append(Finding("orphan", "warning", f"{inst.ref} running at ${inst.hourly_price_usd:.3f}/h with no active job",
                                   f"orphan:{inst.ref}:{inst.boot_id}", suggested_action="stop_instance",
                                   evidence={"hourly": inst.hourly_price_usd, "in_ledger": f.in_ledger}))
        elif job and job.status == JobStatus.RUNNING and hb and quiet and (f.t - hb.ts) > timedelta(minutes=g["idle_grace_minutes"]):
            out.append(Finding("idle", "warning", f"{inst.ref}: job {job.job_id} idle ≥ {g['idle_grace_minutes']} min",
                               f"idle:{inst.ref}:{inst.boot_id}", suggested_action="stop_instance"))

    # B3 patrol — trends that become incidents later if nobody acts (all two-condition, all from the last 30 heartbeats)
    if job and job.status == JobStatus.RUNNING and inst and inst.status == InstanceStatus.RUNNING and len(f.hbs) >= 12 and f.run_fin is None:
        import statistics
        hs = f.hbs
        # throughput: median step/s of the last 5 vs the median of the 20 before, while the GPU/CPU is busy → not a stall, a slowdown
        rates = [h.step_per_s for h in hs if h.step_per_s]
        if len(rates) >= 12:
            recent, before = statistics.median(rates[-5:]), float((job.expect or {}).get("baseline_step_per_s") or statistics.median(rates[-25:-5] or rates[:-5]))
            busy = (hb.gpu_util or 0) >= 30 or (hb.cpu_pct or 0) >= 30
            if before > 0 and recent < 0.6 * before and busy:
                out.append(Finding("throughput_drop", "warning", f"{job.job_id}: {recent:.3g} step/s vs baseline {before:.3g} (−{(1-recent/before)*100:.0f}%) while busy",
                                   f"thr:{job.job_id}:{hb.run_id}:{int(before*1000)}", needs_llm=True, suggested_action="notify",
                                   evidence={"recent_step_per_s": recent, "baseline_step_per_s": before, "gpu": hb.gpu_util, "cpu": hb.cpu_pct}))
        # grad-norm spike: last > 10× median of the window → early warning before NaN
        gns = [h.grad_norm for h in hs if h.grad_norm is not None]
        if len(gns) >= 10 and hb.grad_norm is not None:
            med = statistics.median(gns[:-1])
            if med > 0 and hb.grad_norm > 10 * med:
                out.append(Finding("grad_spike", "warning", f"{job.job_id}: grad_norm {hb.grad_norm:.3g} = {hb.grad_norm/med:.0f}× median {med:.3g} at step {hb.step}",
                                   f"grad:{job.job_id}:{hb.run_id}:{hb.step}", suggested_action="notify", evidence={"grad_norm": hb.grad_norm, "median": med}))
        # plateau: ≥ 2 h of steps advancing while the loss barely moves (std < 1 % of mean) — not an error, a waste
        window = [h for h in hs if (f.t - h.ts) <= timedelta(hours=2)]
        losses = [h.loss for h in window if h.loss is not None]
        steps = [h.step for h in window if h.step is not None]
        if len(losses) >= 10 and steps and steps[-1] > steps[0] and (window[-1].ts - window[0].ts) >= timedelta(minutes=100):   # ≈ the 2 h window, allowing for the sampling gap
            mean = statistics.mean(losses)
            if mean and statistics.pstdev(losses) < 0.01 * abs(mean):
                out.append(Finding("plateau", "info", f"{job.job_id}: loss flat at {mean:.4g} for {(window[-1].ts - window[0].ts).total_seconds()/3600:.1f} h (steps {steps[0]}→{steps[-1]})",
                                   f"plateau:{job.job_id}:{hb.run_id}:{int(mean*1e4)}", suggested_action="notify", evidence={"mean": mean, "std": statistics.pstdev(losses)}))
        # disk trend: linear fit of free GB over the window → hours until below the need threshold
        pts = [(h.ts, h.disk_avail_gb) for h in hs if h.disk_avail_gb is not None]
        if len(pts) >= 10 and pts[-1][1] > 0:
            t0 = pts[0][0]; xs = [(t - t0).total_seconds() / 3600 for t, _ in pts]; ys = [g for _, g in pts]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            den = sum((x - mx) ** 2 for x in xs)
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0     # GB per hour
            need = max(2 * float((job.expect or {}).get("ckpt_size_bytes", 0)) / 1e9, 5.0)
            if slope < -0.05 and pts[-1][1] > need:
                hours = (pts[-1][1] - need) / -slope
                if hours < 3:
                    out.append(Finding("disk_trend", "warning", f"{job.job_id}: disk free {pts[-1][1]:.1f} GB falling {-slope:.2f} GB/h → below {need:.0f} GB in {hours:.1f} h",
                                       f"disktrend:{job.job_id}:{hb.run_id}", suggested_action="clean_disk", evidence={"slope_gb_per_h": slope, "hours_left": hours},
                                       action_params={"keep": 2, "min_free_gb": need}))
        # VRAM creep: used memory rising monotonically by > 20 % across the window with the step advancing → leak before the OOM
        vr = [h.vram_used_mb for h in hs if h.vram_used_mb]
        if len(vr) >= 10 and vr[0] > 0 and vr[-1] > 1.2 * vr[0] and all(b >= a for a, b in zip(vr[-10:], vr[-9:])):
            out.append(Finding("vram_creep", "warning", f"{job.job_id}: VRAM {vr[0]:.0f}→{vr[-1]:.0f} MB rising monotonically (+{(vr[-1]/vr[0]-1)*100:.0f}%)",
                               f"vram:{job.job_id}:{hb.run_id}", suggested_action="notify", evidence={"from_mb": vr[0], "to_mb": vr[-1], "total_mb": hb.vram_total_mb}))

    # R-loss: NaN/divergen dari denyut (tanpa LLM)
    if hb and hb.loss is not None and job and job.status == JobStatus.RUNNING:
        import math
        if math.isnan(hb.loss) or math.isinf(hb.loss):
            out.append(Finding("nan_loss", "critical", f"{job.job_id}: non-finite loss at step {hb.step}",
                               f"nan:{job.job_id}:{hb.run_id}", needs_llm=True, suggested_action="notify"))
    return out
