# ADR-0005 · A signed harness contract instead of log scraping
**Status:** accepted · **Phase:** 2

## Context
`DONE` markers written by `echo` lied about OOM-killed jobs; `pgrep -f` matched its own shell; `nohup` over ssh hung; a checkpoint written at 15 % was almost used to resume (catalog #5, #6, #7, #8, #20, #24).

## Decision
`wrun` wraps the original command: `flock` for single-instance, `pipefail`, `RUN_START`/`RUN_FIN` markers carrying the child's exit code, run id, boot id, artifact list with sha256, evidence numbers and an HMAC signature. `warden-agent` (systemd, stdlib only) heartbeats every 30 s, uploads artifacts with one background rsync, and polls a command mailbox — no ssh in the critical path. `warden.beat()` reports step/loss/grad_norm from the training loop. Artifacts are written tmp → fsync → rename; `VERIFIED` is written only by Warden after opening the file. Unsigned or exit-less markers are rejected. Trainers resume from the last checkpoint that *opens*, not the newest.

## Consequences
+ A finished run and a preempted run are distinguishable by construction. + Legacy jobs are supported through a log parser with a confidence penalty. − One launch line must change; a signing secret must reach each machine (rotated per ADR-0011).

## Evidence
`harness/`, `harness/MARKER-SPEC.md`; live preemption test that found #26–#28.
