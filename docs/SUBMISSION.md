# Paket submisi (hackathon 31 Agu 2026, Track: Taskmaster)

## Teks Devpost (draf)
**Warden — the SRE agent for long-running compute jobs.**

*Inspiration.* We trained speech and medical-imaging models on rented cloud machines for months. We lost $21 to a checkpoint that was 15 % written when the disk filled up, spent 7.5 hours re-running an evaluation because a spot machine was reclaimed mid-eval, and watched a cron "guardian" fail silently 33 times in a row. Twenty-five distinct failures, each one paid for. Every existing tool watched the *machine*; none watched the *work*.

*What it does.* Warden watches long-running jobs (training, evaluation, pipelines) on Compute Engine and acts on two rules the infrastructure can't see: **a live machine ≠ correct training**, and **finished ≠ intact**. A tiny harness on the machine sends heartbeats and signed completion markers; Warden's Watcher applies deterministic two-signal rules (stale heartbeat *and* idle CPU, `TERMINATED` *and* no completion marker). When log text must be understood, Gemini 3.5 Flash — via Google ADK with a fixed JSON schema — diagnoses it, and every claim is cross-checked against the raw log and heartbeat before anything happens. Artifacts are opened (`torch.load`, CSV/JSONL/NPZ/Parquet parsing, checksums) before a job may be called complete. Actions run under a graduated-autonomy policy (observe → propose → act-and-report → silent), rate limits, a circuit breaker, dry-run previews and explicit blast radius; deleting anything is impossible by IAM and by code. A separate dead-man service with its own identity stops machines if Warden itself goes quiet. Humans approve from a phone with one tap on Discord; a global FREEZE button halts all automation instantly.

*How we built it.* Google ADK (`LlmAgent` + `output_schema`), Gemini 3.5 Flash / 3.5 Flash-Lite / 3.7 Flash via Vertex AI, Cloud Run (3 services: core, dashboard, deadman watchdog), Firestore, Pub/Sub, Cloud Scheduler, Secret Manager, Compute Engine, Cloud Storage, Cloud Monitoring, Billing Budgets. Python 3.13, FastAPI, FastAPI + Jinja2.

*Challenges.* Google's frontend intercepts `/healthz`; a client-library regression URL-encoded `(default)`; the boot script is baked into instance metadata; every one of these became a documented lesson.

*Accomplishments.* 41 unit/e2e tests; a chaos suite reproducing all 25 real failure modes (25/25); a live demo job on a real spot machine that is preempted on camera and recovers; a diagnosis of a real NaN crash log for $0.01.

*The dashboard.* A design system first, then code: FastAPI + Jinja2 over a single stylesheet; an inbox-first Overview and an incident page that reads as a narrative with a decision rail (plan → approve → execute → verify). The implementation is measured against the approved mockup pixel by pixel (0.40 % differing) and rendered against production Firestore data before every deploy.

*What we learned.* LLMs should never hold the button. Evidence means opening the file. Success must leave a trace.

*What's next.* Effective-Training-Time-Ratio as the headline metric, IAP in front of the dashboard, second-job onboarding for GPU training with real checkpoints.

## Ceklis
- [ ] Repo dibagikan ke testing@devpost.com + cloudhackathons@google.com (atau publik)
- [x] README teruji dari mesin bersih ≤ 30 mnt — 25 Agu 14:55 WIB: clone segar → venv → pip → 41 tes + chaos 25/25 dalam **34 s** (belum termasuk deploy Cloud Run ±10 mnt)
- [x] Diagram arsitektur (Mermaid di README + PNG `docs/architecture.png`) — 25 Agu 15:05: label ber-kurung dibungkus kutip (sebelumnya parse error di Mermaid 11)
- [ ] Video ≤ 4:00 di YouTube (publik, CC Inggris), Console terlihat, satu take
- [ ] URL hosted: dashboard `warden-ui` (read-only untuk juri)
- [ ] Deskripsi Devpost (teks di atas), track Taskmaster, model id eksplisit
- [ ] Bonus: post sosial #AllThingsAgenticHackathon (+0,2), blog dari jurnal (+0,2)
