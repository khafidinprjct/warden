# Warden — checklist to "complete" (not MVP, not demo)

Status legend: ✅ proven · ◐ partial · ☐ open. Each item names the evidence that proves it. This file replaces STATUS-FASE.md as the status of record.

## A. Job lifecycle — start to finish without guidance
- ✅ A1 One job spec is enough: Warden creates the machine, installs the harness, runs the job. *Gate: spec → training running + first heartbeat, zero human commands.*
- ✅ A2 Machine choice by Warden (type/zone from quota, stock, price, preempt history). *Gate: empty zone → Warden picks another by itself.*
- ✅ A3 Preflight before expensive work (disk, CUDA, deps, .so) — failure stops loudly.
- ✅ A4 Phase-aware resume, both halves proven live. Training resume: drill #5 (26 Aug, resumed from ckpt_000600 after an OOM). **Eval/export resume: 27 Aug, `chaos/live_phase_resume.py` (`chaos/live_phase_resume_report.json`)** — a real Spot preemption (`compute.instances.preempted` recorded by GCE, verified before the gate proceeds) struck at **eval fold 4 of 10 with 262 s of the phase left**, after training had finished. Warden opened `preempted` (no RUN_FIN), executed `start_instance` **AUTO / DONE with no human**, the machine came back, and run `r20260827T154650` re-entered the eval phase — **lowest step observed after the restart = 400 = the full step count, so training was not re-run** — through to COMPLETE with `eval.jsonl` 10 rows and `pred.csv` 2001 rows opened and VERIFIED. Report: $0.0034, ETTR 0.11, 2 incidents, 1 resolved by Warden, 0 needed a human, $0 Gemini (the path is deterministic). Attempt #1 failed and proved nothing — catalogue #38.
- ✅ A5 Harvest: every promised artifact lands in Storage, is opened, verified, VERIFIED by Warden (verifier exists; automatic harvest at job end missing).
- ✅ A6 Close-out: machine STOPPED, final cost, final report + postmortem, human notified — automatically when the job completes.
- ✅ A7 Smoke must load the real components; a smoke that omits declared members is rejected.

## B. Observation — patrol, not only reaction
- ✅ B1 Machine heartbeat + training heartbeat + phase markers + preempt notice.
- ✅ B2 Deterministic detection: VM down, invalid marker, bad artifact, orphan, idle, two-condition stuck, duplicate process.
- ✅ B3 Trends: throughput vs baseline, loss plateau/rise, grad-norm spike, disk-to-full projection, VRAM creep. *Gate: each raises a warning before it becomes an incident.*
- ✅ B4 Preempt storm: 3 preemptions in 60 min → no further start, relocate to another zone (L1); persistent storm → on-demand exit rung, denied by the price guard unless the job's policy allows it. Start rate limits (3/h, 8/day) remain the backoff.
- ✅ B5 Expectations learned per job (artifact size, step rate, heartbeat interval) — not guessed thresholds.

## C. Diagnosis & investigation
- ✅ C1 Structured Diagnostician + deterministic cross-check + second opinion.
- ✅ C2 Investigator with read-only tools (per-run log, heartbeats, artifacts, history, instance).
- ✅ C3 No artificial limits; framework defaults only.
- ✅ C4 Gold evaluation set from real logs (NaN, OOM, wake-loop, kernel fallback, deps) — action accuracy ≥ 90 %, zero fabricated evidence, re-run on every prompt/model change. **Proven through the scheduled path** 27 Aug (rev 00023-pl5): Cloud Scheduler → `/eval` → `eval/2026-08-27T144330Z` = **11/11, accuracy 1.0, 0 fabricated, $0.0695**, health `gold_eval` green, no human step. Until that day the nightly leg had never run: 401 every night (catalogue #35), then three layers of a missing gold set in the image (#36).
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
Evidence: tests/test_recovery_fake.py, chaos 25/25, live drill #5 (M2).

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
- ◐ H5 Soak: 7-day window measured 27 Aug — **26 incidents, 12 actions, 0 false actions** (`eval/soak-20260827`); 7 resolved by Warden, 8 needed a human. Measured twice: locally (`python -m chaos.soak --days 7`) and through the scheduled `/soak`, which returned **HTTP 200** only after the audience fix (catalogue #35 — the nightly job had been failing 401 since 25 Aug). The window closes 1 Sep.

## I. Security & credentials
- ✅ I1 Separate SAs, minimal roles, OIDC push, HMAC ingest, Secret Manager.
- ✅ I2 IAM condition on the core SA: instances must be named `warden-*` (`infra/iam_condition.sh`). Proven 26 Aug by impersonation: add-labels on `demo-train-1` → 403; on `warden-live-1923-oom` → label written; project-level list still works; Warden's compute health stayed green.
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
- ✅ M2 Live on Compute Engine (26 Aug, drill #5, `chaos/live_lifecycle_report.json`): resume_smaller_batch (auto, L2), relocate_zone (snapshot → zone c, source kept TERMINATED), change_machine_type (e2-medium → e2-small), clean_disk (3 checkpoints freed after md5 match), stop — each verified against the world. resize_disk, rollback and kill are proven on the fake provider only.
- ✅ M3 Gold set + nightly evaluation, both proven through Cloud Scheduler on 27 Aug (see C4).
- ✅ M4 Full lifecycle without a human touch (26 Aug, drill #5): spec → VM in 22 s → RUNNING 64 s → OOM at step 600 → diagnosis oom_gpu → resume batch 0.5 (47 s after the incident) → verified → COMPLETE, 22 artifacts opened → report (ETTR 0.73, $0.0017) → machine stopped by close-out. Five earlier drills each found one real defect (catalog #29–#33).

## N. Documentation & reproducibility
- ✅ N1 README, ADR-0014/15/16, architecture diagram (recovery loop, lifecycle API calls, signed mailbox) updated 26 Aug.
- ◐ N2 Clean clone → green, measured end to end on 27 Aug from `main` (a4e1bcc): **77.6 s** — 2.2 s `git clone` · 2.5 s venv · 41 s pip · 27 s **84 tests** · 4.6 s chaos 25/25 (the 36 s quoted on 26 Aug did not include pip). The gate earned its keep: the same clone one commit earlier **failed 2 tests**, because the gold set had never been committed (catalogue #37). **Still open — the half this item also asks for:** deploy-to-live from a *fresh GCP project*. README steps 1–6 are prose, not a script, so that path has never been executed end to end by anyone. Closing it needs a new project (owner's call); the honest alternative is to write `infra/bootstrap_project.sh` first so there is something to time.
- ✅ N3 Operator runbook (docs/RUNBOOK.md, English, incl. Warden self-recovery).
