# ADR-0001 · Three services, three identities
**Status:** accepted (25 Aug 2026) · **Phase:** 0–3

## Context
A single process that watches, decides, acts, serves the UI and watches itself has one failure domain. In our history the watchdog died silently 33 times because it shared a cron PATH with the thing it watched (failure catalog #4).

## Decision
Deploy three Cloud Run services with separate service accounts: `warden-core` (ingest, watcher, pipeline, executor, steward — the only component allowed to act on Compute Engine), `warden-ui` (read Firestore, forward signed actions to core), `warden-deadman` (own identity; stops the fleet if core's heartbeat is absent for 15 minutes). Cloud Scheduler drives core and deadman with OIDC tokens.

## Consequences
+ The UI can be down, slow or redeployed without affecting actions. + The watchdog cannot be taken down by the bug that takes down core. − Three deploys instead of one; shared dependency on Firestore remains (documented limitation).

## Evidence
`warden/main.py`, `warden/deadman.py`, `Procfile*`; deadman gate in `docs/JURNAL-KEPUTUSAN.md` (Phase 6).
