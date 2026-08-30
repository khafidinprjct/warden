# Submission package — All Things Agentic Hackathon (deadline 31 Aug 2026, 5:00 pm PDT)

**Category: The Taskmaster.** Warden intercepts and completes a multi-step background workflow — launch, guard, diagnose,
recover, verify, close out — with no human in the loop, across Compute Engine, Cloud Storage, Firestore, Pub/Sub,
Cloud Scheduler, Vertex AI and Discord.

---

## Devpost text

### Warden — the SRE agent for long-running compute jobs

**Inspiration.** We rent cloud machines to train models for days at a time. We lost a run to a checkpoint that was 15 %
written when the disk filled, re-ran a 7-hour evaluation because a spot machine was reclaimed mid-eval, and watched a
cron "guardian" fail silently 33 times in a row. Every tool we tried watched the *machine*. None watched the *work*.

**What it does.** Warden guards long-running jobs on Compute Engine against two things the infrastructure cannot see:
**a live machine is not correct training**, and **finished is not intact**.

A small harness on the machine emits heartbeats and HMAC-signed completion markers. Warden's Watcher applies
deterministic two-signal rules — stale heartbeat *and* an idle CPU; `TERMINATED` *and* no completion marker — so a slow
job is never confused with a dead one. When log text has to be understood, Gemini 3.5 Flash diagnoses it through Google
ADK against a fixed JSON schema, and **every claim is cross-checked against the raw log and the heartbeat before
anything is allowed to happen**: quotes must exist in the log, line numbers must be in range, the action must match the
category. A diagnosis that fails the cross-check loses its confidence and goes to a human.

Artifacts are opened before a job may be called complete — `torch.load` on checkpoints, CSV/JSONL/NPZ/Parquet parsed,
checksums compared against Storage. "The file exists" is not evidence.

Actions run under graduated autonomy (observe → propose → act-and-report → act silently), per-action rate limits, a
circuit breaker, dry-run previews, and an explicit blast radius on every proposal. **Deleting anything is impossible —
by IAM condition and by code.** A separate dead-man service with its own identity stops machines if Warden itself goes
quiet. A global FREEZE halts all autonomy in one click, from the dashboard or from a phone.

**The loop closes against the world, not against an API return value.** After acting, Warden verifies: did the machine
actually boot, is the step counter advancing, did the disk actually grow, did the new run pass the point it died at? If
not, it moves to the next hypothesis on a per-category ladder and escalates only when the ladder is exhausted.

**How we built it.** Google ADK (`LlmAgent` with `output_schema` and read-only tools), Gemini 3.5 Flash via Vertex AI,
Cloud Run (three services, three identities: core, dashboard, dead-man watchdog), Firestore (state, incident memory and
a vector index for recall), Pub/Sub, Cloud Scheduler, Secret Manager, Compute Engine, Cloud Storage, Cloud Monitoring,
Billing Budgets, and Discord for approvals from a phone. Python 3.12, FastAPI, Jinja2.

**Accomplishments.**
- **106 unit and end-to-end tests**; a chaos suite that reproduces **25 of 25** catalogued failure modes.
- **43 real defects found and closed**, each with what it cost — including five found only by live drills on real
  hardware, and three found in production on the day of submission.
- **A full lifecycle with no human touch, live on Compute Engine**: spec → machine in 22 s → RUNNING in 64 s → GPU OOM
  at step 600 → diagnosis → resume at half the batch size 47 s later → verified against the world → COMPLETE with 22
  artifacts opened → final report → machine stopped. Total cost $0.0017.
- **A real Spot preemption survived mid-evaluation**: preempted at eval fold 4 of 10 with training already finished;
  Warden restarted the machine by itself and the resumed run re-entered evaluation **without re-running training**.
- **A nightly evaluation of the Diagnostician against real crash logs**: 11 of 11 correct category and action, zero
  fabricated citations, $0.07 a night, scheduled and unattended.
- **A 7-day soak: 30 incidents, 0 false actions.**

**A decision worth naming.** Discord is the phone channel — cards, approvals, and `/warden ask` with a photo
attachment. It is built on slash commands and the HTTP interactions endpoint, not on a gateway socket, because a
persistent WebSocket would mean a service that never scales to zero. Warden's core costs almost nothing when the fleet
is quiet, and that property was worth more than the ability to type a bare sentence at the bot. The cost of the choice
is one keystroke: `/warden ask` instead of just typing. Discord requires an answer within three seconds and the
Concierge needs ten to thirty, so the interaction is acknowledged immediately and the tick posts the answer within the
minute — no background thread on Cloud Run, where the CPU is taken away as soon as the response is written.

**Challenges.** Google's frontend answers `/healthz` with its own 404, so a deploy gate that trusted it passed silently.
A nightly job reported `ENABLED` with a fresh `lastAttemptTime` for five days while every run returned 401. The gold
evaluation set was in the repository but not in the container image — and then not in the repository either, because a
blanket `*.log` ignore rule had never let it in. Each one is written down with its cost.

**What we learned.** The LLM never holds the button. Evidence means opening the file. Success has to leave a trace —
a health row that only failure ever writes is a row people stop reading.

**What's next.** Effective-Training-Time-Ratio as the headline metric for a fleet; IAP in front of the dashboard;
onboarding a second GPU training job with real checkpoints.

---

## Required items

| Requirement | Status |
|---|---|
| Gemini 3.5+ via Vertex AI | `gemini-3.5-flash`, verified answering in production today |
| Google agent framework | Google ADK — `LlmAgent`, `output_schema`, 9 read-only tools |
| Google Cloud service | Cloud Run, Firestore, Pub/Sub, Scheduler, Secret Manager, Compute Engine, Cloud Storage, Monitoring, Billing, Vertex AI |
| Category | The Taskmaster |
| Hosted URL | `https://warden-ui-603873318528.us-central1.run.app` |
| Repository | `github.com/khafidinprjct/warden` |
| Spin-up instructions | README "Quick start" — clean clone to green measured at **77.6 s** |
| Architecture diagram | `docs/architecture.png` + Mermaid in README |
| Demo video ≤ 4 min | **outstanding** |

## Checklist
- [x] Repository access for judges — `devposttesting` invited; repository made public
- [x] README tested from a clean clone — 30 Aug: clone → venv → pip → 106 tests + chaos 25/25 in **77.6 s**
- [x] Architecture diagram
- [x] Hosted dashboard URL, reachable without a login
- [x] Discord approvals wired: bot in the server, token in Secret Manager, approver allow-list, signed interactions
- [ ] **Demo video ≤ 4:00 on YouTube** (public, English, Cloud Console visible, unedited live execution)
- [ ] Devpost description submitted with the category selected
- [ ] Bonus: social post with #AllThingsAgenticHackathon (+0.2), write-up from the decision journal (+0.2)

## What the judges are told to look for, and where it is

| Judging language | Where Warden answers it |
|---|---|
| "intercept and complete a multi-step background workflow without human intervention" | `chaos/live_lifecycle_report.json` — drill #5, zero human commands |
| "decouple systems, manage state and memory, secure credentials, handle failures" | three services / three identities; Firestore + vector recall; HMAC, OIDC, Secret Manager, IAM condition that forbids delete; 43 catalogued failures |
| "crash recovery, idempotency, human approvals" | dead-man watchdog; five ticks produce one action (`test_idempotent_ticks_do_not_double_act`); Discord and dashboard approvals on the same policy path |
| "context retrieval and personalization based on past interactions" | incident memory: postmortems, embeddings, Firestore vector index, `remembered_rung` puts the action that worked before first |
| "proper handling of edge cases" | `docs/FAILURE-CATALOG.md` |
