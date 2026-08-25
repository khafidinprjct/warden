# ADR-0003 · Firestore is the source of truth; loops poll, they do not subscribe
**Status:** accepted · **Phase:** 1

## Context
Approvals arrive from a phone minutes after a card is sent; a Cloud Run instance may have been recycled in between. ADK's in-session confirmation cannot survive that.

## Decision
All state lives in Firestore collections with explicit models (`fleet`, `jobs`, `incidents` with a state machine, `decisions`, `evidence`, `audit`, `costs`, `health`, `leases`). LLM sessions are ephemeral. Loops run on a Scheduler cadence and read the store each time; the dashboard reloads every 30 s instead of holding listeners. Per-job leases (transactional, 5-minute TTL) prevent two ticks from acting twice.

## Consequences
+ Any component can die mid-flight and the next tick continues from the store. + No long-lived connections on request-based Cloud Run (cost ≈ 0). − Latency is bounded by the tick interval (see ADR-0012). − The dashboard is not real-time.

## Evidence
`warden/store/firestore.py`, `warden/core/state_machine.py`.
