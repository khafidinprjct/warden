# ADR-0008 · An external watchdog with its own identity
**Status:** accepted · **Phase:** 6

## Context
A system that only speaks when it fails is indistinguishable from a dead system (catalog #4).

## Decision
The Watcher writes a heartbeat on every tick. `warden-deadman`, a separate Cloud Run service with its own service account and Scheduler trigger, checks that heartbeat every 5 minutes; if it is older than 15 minutes it stops every `warden-managed` instance and records the action. A Cloud Monitoring absence alert on the same heartbeat (aggregated per service, not per revision) e-mails the owner.

## Consequences
+ A dead Warden cannot leave machines burning. − A Firestore outage blinds both; the watchdog then errs toward stopping, which is the safe direction.

## Evidence
`warden/deadman.py`; alert policy 2282888712998204522 (false positives per revision fixed 25 Aug).
