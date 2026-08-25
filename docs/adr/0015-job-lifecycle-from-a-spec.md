# ADR-0015 · Warden owns the job from a spec to the final report
**Status:** accepted (26 Aug 2026) · **Checklist:** A

## Context
Warden guarded machines that our own shell scripts had created. "From start to finish, without you guiding each step" needs the agent to own the whole lifecycle: create, guard, harvest, verify, close.

## Decision
`warden/lifecycle.py` + `POST /jobs/launch` + `warden launch spec.yaml` + the dashboard form:
1. **Ledger first** (P7): the job document exists before any API call, so a half-launched machine can never be an orphan.
2. **Zone choice**: candidates in order, skipping zones with a stock-out in the last 30 minutes and regions without CPU-quota headroom; a `ZONE_RESOURCE_POOL_EXHAUSTED` moves to the next zone and records the stock-out for the recovery ladder.
3. **Machine**: spot by default, `instance_termination_action=STOP`, boot disk never auto-deleted, label `warden-managed=true`, service account `warden-vm`, metadata carrying the job id, core URL, HMAC, bucket, harness location, resume command and the startup script; `warden-env-*` keys become the job's environment.
4. **Alive** = first heartbeat (`PENDING → RUNNING`). Preflight failure, an incomplete smoke, 80 %/100 % of the job budget are rules with actions (stop) rather than log lines.
5. **Close-out**: artifacts verified → `COMPLETE` → final report (`reports/<job>`: spend, ETTR, artifacts, incidents resolved by Warden vs. by a human, LLM cost) → rule `complete_running` stops the machine on the next tick.

## Consequences
- Names are `warden-<job_id>`; an IAM condition on that prefix can bound the core service account.
- The old `infra/vm_create.sh` remains for manual experiments; it is no longer on the path.
