# ADR-0006 · Graduated autonomy per action, with a circuit breaker
**Status:** accepted · **Phase:** 3

## Context
A babysitter that restarted a machine an operator had deliberately stopped had to be rebuilt (catalog #25). Blanket autonomy is wrong; blanket approval is useless at 3 a.m.

## Decision
Every action type has a level: L0 observe, L1 propose (Discord/dashboard card, expires in 30 minutes → escalate), L2 act then report, L3 act silently. The policy engine is a pure function `evaluate(action, ctx, policy) → Decision` with an `explain` trace; limits per hour/day, cost caps, price-increase caps, a legacy-job penalty, a low-confidence penalty, operator hold and ssh-session detection, a global daily auto-spend cap, and a per-job circuit breaker (> 3 automatic actions per hour or 2 failed verifications → everything drops to L1 for 60 minutes). Every decision carries a dry-run plan and a blast radius. Expired decisions can be re-evaluated against the current context; "Always 24 h" grants a temporary L2 override for one job+action.

## Consequences
+ Autonomy is earned per action, not granted globally; the trace is visible on every card. − A tripped breaker can hold a legitimate restart until a human approves (observed on 25 Aug: the breaker asked for approval after two quarantines and a notify; notify no longer counts).

## Evidence
`warden/policy/engine.py`, `policies.yaml`, `tests/test_policy.py` (matrix), journal 25 Aug 15:15.
