# ADR-0014 · Every action is verified against the world; failure moves to the next hypothesis
**Status:** accepted (26 Aug 2026) · **Checklist:** D, E

## Context
Until now an incident went `EXECUTING → VERIFYING → RESOLVED` in the same function call, on the strength of the provider API's return value ("start returned RUNNING"). That is a claim, not evidence: a started VM whose harness never comes back, a resume command nobody reads, a cleaned disk that is still full — all of them would have been "RESOLVED". Four mailbox actions (resume, kill, quarantine, rollback) were in fact writing instance metadata that no harness read; they had never worked in production. And every recommendation the Diagnostician could make had to map onto one of five executors, so "resume with a smaller batch" silently became "resume".

## Decision
1. **One executor per action** (`executor/registry.py`): start, stop, resume (same / smaller_batch / fewer_workers / clean), kill, quarantine, rollback_last_good (newer checkpoints set aside, never deleted), clean_disk (local checkpoints whose md5 matches the Storage copy, keep N), relocate_zone (snapshot → disk in the target zone → new instance; source kept STOPPED), change_machine_type (stop → setMachineType → start), resize_disk (+ `grow_fs`). No delete exists. Every action has a `dry_run` plan and intent/result audit.
2. **Signed mailbox** — machine-side actions are documents in `cmd/<job>` signed by warden-core (HMAC over cmd/args/decision/ts/nonce). The harness rejects unsigned commands and reports every outcome to `/ingest/cmd_result` (nonce, ok, detail, freed bytes…).
3. **Verification against the world** (`executor/recovery.py`): after execution the incident is `VERIFYING` with a spec (kind, deadline from policy, baseline boot/run/step/disk). Each tick checks facts: instance status, a heartbeat from a *new boot*, the step advancing across ≥ 2 heartbeats, the new run's `RUN_FIN`, disk free space, the harness result. Only then `RESOLVED`.
4. **Hypothesis ladders** — per category/rule (plan §5.3): `oom_gpu` = batch 0.5 → batch 0.25 → bigger machine; `nan_divergence` = rollback+lr 0.5 → two back+lr 0.25 → stop; `disk_full` = clean → resize; `preempted` = start → relocate; `stuck` = kill+resume → clean restart; permanent categories = stop + patch suggestion. The Diagnostician's own recommendation is rung 1; a remembered postmortem of the same pattern (same job first, then other jobs) puts the proven action in front. When a rung's verification fails the next rung is policy-evaluated and executed or queued for approval; when the ladder is exhausted (or `recovery.max_attempts_per_incident`) the incident escalates with the reason.
5. **Operator requests** (`/jobs/{id}/propose`) go through the same policy → dry-run → approval → verification path. There is no side door.

## Consequences
- `RESOLVED` now means "the world confirmed it"; the dashboard shows attempts, the verification result and the remaining hypotheses.
- The state machine gained `FALSE_POSITIVE` from `AWAITING_APPROVAL`/`ESCALATED`; a human dismissal is memory (ADR-0016).
- Live proof of the new executors on real Compute Engine is a separate gate (`chaos/live_lifecycle.py`); the fake provider proves the control flow (`tests/test_recovery_fake.py`, chaos 25/25).
