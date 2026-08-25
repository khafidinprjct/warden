# ADR-0012 · One-minute tick and measured SLOs
**Status:** accepted (25 Aug 2026) · **Phase:** 13

## Context
The Watcher ran every 2 minutes; a detection SLO of 60 s was therefore unattainable by design.

## Decision
`warden-tick` runs every minute. Core emits structured events (`warden.incident` with `detect_ms`, `warden.decision` with `decision_ms`, `warden.llm` with cost and latency) that feed log-based metrics, a Monitoring dashboard and three SLOs on a custom service: decision ≤ 30 s (99 %), detection ≤ 60 s (90 %), watcher heartbeat in every 5-minute window (99 %), rolling 7 days.

## Consequences
+ Detection latency is bounded by ~60 s plus provider propagation, and it is measured rather than assumed. − 1 440 Cloud Run requests per day (still within free tier); cold starts appear as a latency tail (p95 alert uses a 5-minute window).

## Evidence
`docs/OBSERVABILITY.md`; metric `warden_tick_ms` first samples 1.3–2.6 s warm, 8–14 s on cold start.
