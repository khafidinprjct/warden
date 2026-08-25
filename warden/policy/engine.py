"""Mesin kebijakan: fungsi MURNI (tanpa I/O) supaya bisa diuji matriks penuh.
evaluate(action, ctx) -> Decision(verdict, autonomy, explain[], blast_radius, cost)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from warden.core.models import Action, Autonomy, BlastRadius, Decision, Verdict, now

DEFAULT_POLICY_PATH = Path(__file__).with_name("policies.yaml")


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


@dataclass
class Ctx:
    """Semua fakta yang dibutuhkan keputusan — disiapkan pemanggil (tanpa I/O di dalam engine)."""
    job_id: str
    instance_ref: str = ""
    hourly_price_usd: float = 0.0
    action_cost_usd: float = 0.0
    actions_last_hour: int = 0          # tindakan jenis ini pada job ini, 1 jam terakhir
    actions_today: int = 0
    auto_actions_last_hour: int = 0     # SEMUA tindakan otomatis pada job ini, 1 jam terakhir
    failed_verifications_in_row: int = 0
    auto_spend_today_usd: float = 0.0
    operator_hold_until: datetime | None = None
    operator_active: bool = False
    stock_ok: bool | None = None        # untuk start/relocate
    boot_disk_auto_delete: bool | None = None
    managed: bool = True
    circuit_open_until: datetime | None = None
    llm_confidence: float | None = None
    autonomy_overrides: dict[str, str] = field(default_factory=dict)
    legacy_job: bool = False
    price_increase_pct: float = 0.0
    frozen: bool = False                # tombol merah global (R2)


BLAST = {
    Action.NOTIFY: BlastRadius.NONE,
    Action.START_INSTANCE: BlastRadius.THIS_RUN,
    Action.RESUME_JOB: BlastRadius.THIS_RUN,
    Action.STOP_INSTANCE: BlastRadius.THIS_JOB,
    Action.QUARANTINE_ARTIFACT: BlastRadius.ARTIFACTS,
    Action.ROLLBACK_LAST_GOOD: BlastRadius.ARTIFACTS,
    Action.RELOCATE_ZONE: BlastRadius.THIS_JOB,
    Action.RESIZE_DISK: BlastRadius.BUDGET,
    Action.KILL_PROCESS: BlastRadius.THIS_RUN,
    Action.CHANGE_MACHINE_TYPE: BlastRadius.BUDGET,
}


def evaluate(action: Action, ctx: Ctx, policy: dict[str, Any], t: datetime | None = None) -> Decision:
    t = t or now()
    ex: list[str] = []
    level = Autonomy(ctx.autonomy_overrides.get(action.value) or policy["autonomy"].get(action.value, "L1"))
    lim = policy.get("limits", {}).get(action.value, {})
    g = policy["global"]
    d = Decision(job_id=ctx.job_id, action=action, autonomy=level, blast_radius=BLAST[action],
                 cost_usd=ctx.action_cost_usd)

    def deny(reason: str) -> Decision:
        ex.append(f"DENY: {reason}"); d.verdict = Verdict.DENY; d.explain = ex; return d

    def hold(reason: str) -> Decision:
        ex.append(f"HOLD: {reason}"); d.verdict = Verdict.HELD; d.explain = ex; return d

    # --- pagar keras ---
    if action.value in policy.get("hard_deny", []):
        return deny("action is hard-denied")
    if not ctx.managed:
        return deny("instance not labeled warden-managed")
    if action in (Action.STOP_INSTANCE, Action.START_INSTANCE) and ctx.boot_disk_auto_delete is True:
        return deny("boot disk auto-delete=true (unsafe config, P8)")
    ex.append(f"hard guards passed; base level {level}")

    # --- tahan: manusia sedang di mesin / hold / freeze ---
    if ctx.frozen and action != Action.NOTIFY:
        return hold("Warden FROZEN (red button)")
    if ctx.operator_hold_until and ctx.operator_hold_until > t and action != Action.NOTIFY:
        return hold(f"operator_hold until {ctx.operator_hold_until.isoformat()}")
    if ctx.operator_active and action in (Action.STOP_INSTANCE, Action.KILL_PROCESS, Action.RESUME_JOB, Action.ROLLBACK_LAST_GOOD):
        return hold("operator active on machine (ssh session)")

    # --- circuit breaker ---
    cb = policy["circuit_breaker"]
    if ctx.circuit_open_until and ctx.circuit_open_until > t:
        level = min(level, Autonomy.L1, key=list(Autonomy).index); ex.append("circuit breaker OPEN → downgraded to L1")
    elif ctx.auto_actions_last_hour >= cb["max_auto_actions_per_hour"] or ctx.failed_verifications_in_row >= cb["max_failed_verifications_in_row"]:
        level = min(level, Autonomy.L1, key=list(Autonomy).index); ex.append("circuit breaker tripped (actions/hour or failed verifications) → L1")

    # --- batas laju & biaya ---
    if "per_hour" in lim and ctx.actions_last_hour >= lim["per_hour"]:
        return deny(f"limit {lim['per_hour']}/hour reached")
    if "per_day" in lim and ctx.actions_today >= lim["per_day"]:
        return deny(f"limit {lim['per_day']}/day reached")
    if "max_cost_usd" in lim and ctx.action_cost_usd > lim["max_cost_usd"]:
        level = min(level, Autonomy.L1, key=list(Autonomy).index); ex.append(f"cost ${ctx.action_cost_usd:.2f} > ${lim['max_cost_usd']} → L1")
    if "max_price_increase_pct" in lim and ctx.price_increase_pct > lim["max_price_increase_pct"]:
        return deny(f"price increase {ctx.price_increase_pct:.0f}% > {lim['max_price_increase_pct']}%")
    if action != Action.NOTIFY and ctx.auto_spend_today_usd + ctx.action_cost_usd > g["auto_spend_daily_cap_usd"]:
        level = min(level, Autonomy.L1, key=list(Autonomy).index); ex.append("daily auto-spend cap → L1")

    # --- prasyarat tindakan ---
    if action in (Action.START_INSTANCE, Action.RELOCATE_ZONE) and ctx.stock_ok is False:
        return deny("no machine stock in target zone")
    if ctx.legacy_job and action in (Action.STOP_INSTANCE, Action.KILL_PROCESS, Action.ROLLBACK_LAST_GOOD, Action.QUARANTINE_ARTIFACT):
        level = min(level, Autonomy.L1, key=list(Autonomy).index); ex.append("legacy job (synthetic signals) → L1")
    if ctx.llm_confidence is not None and ctx.llm_confidence < 0.7 and action not in (Action.NOTIFY, Action.START_INSTANCE, Action.STOP_INSTANCE):
        level = min(level, Autonomy.L1, key=list(Autonomy).index); ex.append(f"confidence {ctx.llm_confidence:.2f} < 0.7 → L1")

    d.autonomy = level
    if level == Autonomy.L0:
        d.verdict = Verdict.DENY; ex.append("L0: observe only")
    elif level == Autonomy.L1:
        d.verdict = Verdict.NEED_APPROVAL
        d.expires_at = t + timedelta(minutes=g["approval_ttl_minutes"]); ex.append("L1: human approval required")
    else:
        d.verdict = Verdict.AUTO; ex.append(f"{level}: automatic")
    d.explain = ex
    return d
