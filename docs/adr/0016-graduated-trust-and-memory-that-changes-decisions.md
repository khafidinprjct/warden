# ADR-0016 · Graduated trust, and memory that changes decisions
**Status:** accepted (26 Aug 2026) · **Checklist:** B, F, G

## Decision
- **Promotion / demotion** (`steward.apply_promotions`, every 10 min): an L1 action approved `promotion.streak` (5) times in a row for a job without a failure or rejection is promoted to L2 for that job; an L2 action whose verification failed is demoted to L1. Both are audit entries and notifications, and both are reversible by editing `policies/<job_id>` (per-job overrides of autonomy and limits) or `policies.yaml`.
- **Every limit is visible**: a rate limit, cost cap, breaker, price guard, freeze or hold that changes a decision emits `warden.limit`.
- **Manual mode**: `POST /jobs/{id}/hold?minutes=` (dashboard button) — Warden keeps watching and takes no action on that job.
- **Memory lowers noise**: `false_positive` is a state; two dismissals of the same rule on the same job in 7 days make Warden withhold the proposed action (notification only, severity info).
- **Memory shapes the plan**: the action that resolved the same category on this job — or, failing that, on another job — becomes rung 1 of the ladder and is cited on the decision and the incident.
- **Learned baselines**: the steward writes `baselines/<job>` (median step rate, p95 heartbeat interval, checkpoint size from VERIFIED markers) and fills `job.expect` where the operator set nothing; the trend rules use them.
- **Patrol**: throughput drop vs baseline while busy (→ diagnosis), gradient spike, loss plateau, disk-fill projection (→ proactive clean_disk), VRAM creep — warnings before the incident.

## Non-decision
Warden does not ration its own reasoning. There is no daily LLM cap and no tool-call budget; the framework defaults are the only guard. Warden manages the user's GPU/CPU spend, not its own tokens (owner decision, 26 Aug).
