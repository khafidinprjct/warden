from datetime import timedelta
from warden.core.models import Action, Autonomy, Verdict, now
from warden.policy.engine import Ctx, evaluate, load_policy

P = load_policy()


def ctx(**kw):
    base = dict(job_id="j1", instance_ref="us-central1-a/vm1", hourly_price_usd=0.03, managed=True, stock_ok=True)
    base.update(kw); return Ctx(**base)


def test_start_after_preempt_is_auto():
    d = evaluate(Action.START_INSTANCE, ctx(), P)
    assert d.verdict == Verdict.AUTO and d.autonomy == Autonomy.L2


def test_delete_never_exists():
    assert not any(a.value.startswith("delete") for a in Action)


def test_unmanaged_denied():
    assert evaluate(Action.STOP_INSTANCE, ctx(managed=False), P).verdict == Verdict.DENY


def test_unsafe_disk_config_denied():
    assert evaluate(Action.STOP_INSTANCE, ctx(boot_disk_auto_delete=True), P).verdict == Verdict.DENY


def test_rate_limit_denies():
    assert evaluate(Action.START_INSTANCE, ctx(actions_last_hour=3), P).verdict == Verdict.DENY


def test_circuit_breaker_downgrades_to_approval():
    d = evaluate(Action.START_INSTANCE, ctx(auto_actions_last_hour=3), P)
    assert d.verdict == Verdict.NEED_APPROVAL and d.expires_at is not None


def test_operator_hold_holds():
    d = evaluate(Action.RESUME_JOB, ctx(operator_hold_until=now() + timedelta(hours=1)), P)
    assert d.verdict == Verdict.HELD


def test_freeze_holds_everything_but_notify():
    assert evaluate(Action.STOP_INSTANCE, ctx(frozen=True), P).verdict == Verdict.HELD
    assert evaluate(Action.NOTIFY, ctx(frozen=True), P).verdict == Verdict.AUTO


def test_low_confidence_needs_human():
    assert evaluate(Action.ROLLBACK_LAST_GOOD, ctx(llm_confidence=0.5), P).verdict == Verdict.NEED_APPROVAL


def test_legacy_job_destructive_needs_human():
    assert evaluate(Action.STOP_INSTANCE, ctx(legacy_job=True), P).verdict == Verdict.NEED_APPROVAL


def test_stockout_denies_start():
    assert evaluate(Action.START_INSTANCE, ctx(stock_ok=False), P).verdict == Verdict.DENY


def test_daily_cap_downgrades():
    d = evaluate(Action.START_INSTANCE, ctx(auto_spend_today_usd=9.9, action_cost_usd=0.5), P)
    assert d.verdict == Verdict.NEED_APPROVAL


def test_explain_always_present():
    for a in Action:
        assert evaluate(a, ctx(), P).explain
