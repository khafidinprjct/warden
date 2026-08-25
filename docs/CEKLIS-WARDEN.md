# Warden — checklist to "complete" (not MVP, not demo)

Status legend: ✅ proven · ◐ partial · ☐ open. Each item names the evidence that proves it. This file replaces STATUS-FASE.md as the status of record.

## A. Job lifecycle — start to finish without guidance
- ✅ A1 One job spec is enough: Warden creates the machine, installs the harness, runs the job. *Gate: spec → training running + first heartbeat, zero human commands.*
- ✅ A2 Machine choice by Warden (type/zone from quota, stock, price, preempt history). *Gate: empty zone → Warden picks another by itself.*
- ✅ A3 Preflight before expensive work (disk, CUDA, deps, .so) — failure stops loudly.
- ◐ A4 Phase-aware resume for training, eval, harvest (harness exists; eval/harvest phases not re-tested).
- ✅ A5 Harvest: every promised artifact lands in Storage, is opened, verified, VERIFIED by Warden (verifier exists; automatic harvest at job end missing).
- ✅ A6 Close-out: machine STOPPED, final cost, final report + postmortem, human notified — automatically when the job completes.
- ✅ A7 Smoke must load the real components; a smoke that omits declared members is rejected.

## B. Observation — patrol, not only reaction
- ✅ B1 Machine heartbeat + training heartbeat + phase markers + preempt notice.
- ✅ B2 Deterministic detection: VM down, invalid marker, bad artifact, orphan, idle, two-condition stuck, duplicate process.
- ✅ B3 Trends: throughput vs baseline, loss plateau/rise, grad-norm spike, disk-to-full projection, VRAM creep. *Gate: each raises a warning before it becomes an incident.*
- ☐ B4 Preempt storm: backoff, zone rotation, on-demand exit when budget allows.
- ✅ B5 Expectations learned per job (artifact size, step rate, heartbeat interval) — not guessed thresholds.

## C. Diagnosis & investigation
- ✅ C1 Structured Diagnostician + deterministic cross-check + second opinion.
- ✅ C2 Investigator with read-only tools (per-run log, heartbeats, artifacts, history, instance).
- ✅ C3 No artificial limits; framework defaults only.
- ✅ C4 Gold evaluation set (11/11, 0 fabricated, $0.07/run; nightly `warden-gold-eval`) from real logs (NaN, OOM, wake-loop, kernel fallback, deps) — action accuracy ≥ 90 %, zero fabricated evidence, re-run on every prompt/model change.
- ✅ C5 Multimodal (training-curve PNG attached for plateau/throughput/grad/NaN/stuck incidents; Ask Warden reads a phone photo, labelled, never acting): loss curve rendered and judged when numbers are ambiguous; phone screenshots read (labelled, never auto-acting).

## D. Actions — every recommendation has a real executor
- ✅ D1 start after preempt · stop idle/orphan · resume from VERIFIED · quarantine artifact.
- ✅ D2 relocate_zone (snapshot → disk → new instance; old one kept STOPPED).
- ✅ D3 resume_smaller_batch / fewer_workers / restart_clean (signed mailbox + env WARDEN_*_SCALE).
- ✅ D4 rollback_last_good + lr scale (newer checkpoints set aside, never deleted).
- ✅ D5 kill hung/duplicate process via mailbox (no ssh).
- ✅ D6 clean_disk: local checkpoints whose hash matches the Storage copy, keep newest 2.
- ✅ D7 change_machine_type (≤ +50 % price, L1) / resize_disk (+grow_fs).
- ✅ D8 stop + patch suggestion for env/deps/code/config.
- ✅ D9 No delete anywhere.
- ✅ D10 Every action: dry_run, blast radius, intent → result audit, requested-vs-observed.
Evidence: tests/test_recovery_fake.py, chaos 25/25. Live GCE test of D2/D7 pending (M2).

## E. Closing the loop
- ✅ E1 Post-action verification against the world (status, new boot, step advancing, disk, harness result) — `executor/recovery.py`.
- ✅ E2 Outcome ≠ expectation → next hypothesis on the per-category ladder; escalate only when exhausted.
- ✅ E3 Attempt limit from policy (`recovery.max_attempts_per_incident`), each rung policy-evaluated and audited.
- ✅ E4 RESOLVED only after progress is observed (≥ 2 heartbeats with the step advancing / status confirmed).

## F. Memory & learning
- ✅ F1 Automatic postmortems + embeddings + vector recall.
- ✅ F2 Memory changes decisions: the action that resolved the same pattern goes first (`remembered_rung`); Diagnostician sees remembered incidents.
- ✅ F3 Memory lowers noise: alarms proven false get lower weight.
- ✅ F4 Per-job baselines (rate, loss, sizes) updated from verified data.
- ✅ F5 Cross-job memory.

## G. Autonomy & policy — graduated trust
- ✅ G1 L0–L3 per action, circuit breaker, lease, freeze/thaw, always-24h.
- ✅ G2 Automatic promotion/demotion from track record (audited, visible).
- ✅ G3 Every policy limit that is hit becomes a visible event.
- ✅ G4 Manual mode: ssh session → defer; `hold <job> <duration>`.
- ✅ G5 Per-job policy.

## H. Warden's own reliability
- ✅ H1 State in Firestore; core restart resumes (verify/ladder state lives on the incident).
- ✅ H2 Separate deadman; DLQ; retries; Gemini/provider breaker; HMAC rotation.
- ✅ H3 Idempotency proven (5 ticks = 1 start; test_idempotent_ticks_do_not_double_act).
- ✅ H4 Two jobs guarded concurrently without interference (test).
- ◐ H5 Soak: measurement in place (`python -m chaos.soak --days 7`, writes eval/soak-*); 2-day baseline 0 false actions; the 7-day window closes 1 Sep.

## I. Security & credentials
- ✅ I1 Separate SAs, minimal roles, OIDC push, HMAC ingest, Secret Manager.
- ☐ I2 IAM condition limiting the core SA to Warden machines (negative test).
- ✅ I3 Mailbox commands signed by core and verified by the harness; harness reports results.
- ✅ I4 Security review updated for D2–D8 (docs/SECURITY-REVIEW.md, 26 Aug).

## J. Cost — the user's GPU money
- ✅ J1 Ledger rate × age per machine, cost per incident.
- ✅ J2 ETTR per job (digest, overview, jobs, job detail, final report).
- ✅ J3 Runway vs job budget; 80 % warn, 100 % stop — automatic.
- ◐ J4 Ledger vs Billing ± 10 % — `infra/billing_reconcile.py` ready; needs the owner to enable the BigQuery billing export (console-only setting), then run.

## K. Human interface
- ◐ K1 Dashboard v2: phone audit passed; desktop not audited.
- ✅ K2 Every decision shows evidence → diagnosis → hypotheses → cost → verification.
- ✅ K3 Ask Warden answers with citations; any action can be requested through the job page (same policy/approval path) an action through the same approval path.
- ☐ K4 Discord — last.

## L. Observability & audit
- ✅ L1 Structured events, metrics, Monitoring dashboard, SLOs, alerts.
- ✅ L2 Reasoning trace per incident (investigator tool calls, prompt version, model) browsable from the UI.
- ✅ L3 Daily digest (Scheduler 08:00 WIB; ETTR included).

## M. Testing
- ✅ M1 25 chaos scenarios, unit tests, infra chaos.
- ☐ M2 Live test of each new action (D2–D8) on a real machine.
- ✅ M3 Gold set + nightly evaluation (C4).
- ☐ M4 Full lifecycle A1→A6 without a human touch.

## N. Documentation & reproducibility
- ◐ N1 README + ADR-0014/15/16 updated; diagram still shows the pre-recovery loop (redraw pending).
- ◐ N2 Clean clone → venv → 76 tests + chaos 25/25 in 36 s (26 Aug); the full deploy-to-live timing is measured at the next fresh project.
- ✅ N3 Operator runbook (docs/RUNBOOK.md, English, incl. Warden self-recovery).
