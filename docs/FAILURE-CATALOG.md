# Katalog 25 mode kegagalan → detektor Warden → uji

Semua pernah terjadi pada kami (harga = yang benar-benar dibayar). Kolom "uji" = skenario di `chaos/run.py` (25/25 lulus) atau tes unit.

| # | Mode | Harga | Detektor / aturan | Tindakan | Uji |
|---|---|---|---|---|---|
| 1 | Spot dicabut, tidak ada yang menyalakan | 4 jam diam | `preempted` (TERMINATED 2 tick ∧ tanpa RUN_FIN) | `start_instance` L2 → resume | chaos #1, test_tick |
| 2 | Spot dicabut + disk terhapus | 75 klip | `unsafe_config` (auto-delete / DELETE) | notify; start/stop ditolak sampai aman | chaos #2 |
| 3 | Dicabut saat eval | $15 | bukti fase di insiden; `startup.sh` resume sadar fase | resume dari fase terakhir | chaos #3 |
| 4 | Penjaga mati senyap | 33 jendela | denyut watcher + `warden-deadman` + alarm Monitoring absen 10 mnt | STOP mesin bila Warden mati | chaos #4, deadman /check |
| 5 | DONE palsu tanpa exit code | prep hilang | `done_without_exit` | ditolak; job tetap RUNNING | chaos #5 |
| 6 | DONE basi / tanda tangan salah | $0,10 | `marker_invalid` (HMAC, run_id, ts) | ditolak | chaos #6 |
| 7 | Checkpoint korup ukuran identik | nyaris kehilangan model | verifier: sha ≠ sebelumnya, torch.load | karantina L2 | chaos #7, test_verifier |
| 8 | Disk penuh → checkpoint 15 % | nyaris resume dari rusak | `disk_low` preventif; size_vs_expect; resume hanya VERIFIED | notify / karantina | chaos #8 |
| 9 | OOM kasus terburuk | $1,15 | regex OOM + cek silang VRAM | resume batch ↓ (≤2×) | chaos #9 |
| 10 | Salah diagnosis OOM | GPU terbakar | cek silang: klaim tanpa bukti → confidence 0,4, manusia | minta izin | chaos #10 |
| 11 | pip gagal senyap, DONE tetap ditulis | $0,15 | `wrun` exit code proses anak → `run_fin_nonzero` → LLM | stop + patch_suggest | chaos #11 |
| 12 | Image tanpa pip / .so rusak | run gagal | preflight `install.sh` → `PREFLIGHT_FAIL` | tidak meluncurkan | chaos #12 |
| 13 | Fallback kernel senyap | jam GPU | `slow` (basi ∧ sibuk) + Diagnostician throughput | stop + laporan | chaos #13 |
| 14 | Instance yatim | $1,3 | `orphan` (tanpa job/denyut ≥10 mnt) | STOP L2 (bukan delete) | chaos #14 |
| 15 | VM idle | $2 | `orphan`/`idle` dua-syarat ≥15 mnt | STOP L2 | chaos #15 |
| 16 | Create gagal per-item, stderr dipangkas | jadwal | `OpResult.error` terstruktur, diminta-vs-jadi | ESCALATED | chaos #16 |
| 17–18 | Kuota global/regional/disk | pengajuan sia-sia | `quota()` GLOBAL: vs regional | laporan sebelum meluncurkan | chaos #17–18 |
| 19 | Badai preempt | restart budget | batas 3 start/jam, circuit breaker | minta izin setelah ke-3 | chaos #19 |
| 20 | Proses ganda | risiko OOM | `wrun` flock; `dup_process` (ppid) | kill L1 | chaos #20 |
| 21 | Artefak tertahan gerbang | run ulang | verifier: artefak tidak tersedia → FINISHED_UNVERIFIED | manusia | chaos #21 |
| 22 | Smoke lolos palsu | $0,10 | `SMOKE_FIN.members ⊇ expected` | tolak smoke | chaos #22 |
| 23 | Smoke menimpa juara | nyaris | sha `champion/**` berubah | ESCALATED | chaos #23 |
| 24 | nohup via ssh gantung | yatim | tidak ada ssh di jalur kritis; mailbox | — | chaos #24 |
| 25 | Balapan dengan operator | VM dibuat ulang | `operator_active` (sesi ssh) → HELD; `/warden hold`; lease | tahan | chaos #25 |
| 26 | **Preempt nyata memotong checkpoint terbaru**; trainer resume dari yang *terbaru*, bukan yang *utuh* (25 Agu 15:02, `ckpt_001700.npz` → `EOFError`, exit 1 dalam 1 dtk) | 1 run gagal, ±25 mnt | trainer: resume mundur ke ckpt yang bisa dibuka, yang rusak → `.corrupt`; handler SIGUSR1 hanya menandai (anti re-entrancy) | — (sisi harness) | uji lokal `toy_train` ckpt terpotong → mundur ✔ |
| 27 | **Denyut basi menimpa run_id**: `train.json` run lama dikirim agen atas nama run baru → `RUN_FIN exit 1` run baru tak pernah dinilai Warden (buta 25 mnt) | deteksi luput | ingest: denyut tak boleh memundurkan run_id (bandingkan ts RUN_START); agen: RUN_START lebih baru dari train.json → hanya kirim run_id+fase | `run_fin_nonzero` kembali terpicu | `tests/test_stale_heartbeat.py` |
| 28 | **Unggahan artefak menyumbat denyut**: setelah boot `_uploaded` kosong → agen `cp` 135 file satu-satu di loop denyut → denyut & marker tertahan 10 mnt (25 Agu 17:58–18:08) — Warden bisa salah menilai harness mati | deteksi tertunda 10 mnt | agen: satu `gcloud storage rsync` di thread latar; loop denyut tak pernah menunggu unggahan | — | agent.log: jeda 10 mnt sebelum vs ≤35 dtk sesudah |

## #29 · Resume command ran under the wrong job id (26 Aug 2026, live drill)
- **Symptom:** Warden-launched job `live-1800-oom` stayed at step 0 while the machine's CPU was busy; the training (and its OOM drill) reported under `toy-train`.
- **Cause:** `startup.sh` launched the resume command with only `WARDEN_HMAC` in the environment; the bootstrap script defaulted `WARDEN_JOB` to `toy-train`. On the old demo VM the metadata happened to say `toy-train`, so it never showed.
- **Fix:** startup exports `WARDEN_JOB / CORE_URL / BUCKET / ENTRY / DIR` to the resume command; bootstraps refuse to run without `WARDEN_JOB` (`${WARDEN_JOB:?}`); test `test_startup_script_exports_job_env_to_resume`.
- **Cost:** ≈ $0.01 VM + 10 min. Detected by the live gate, not by the fake — which is why the live gate exists.

## #30 · "Deployed" is not "serving": the previous Cloud Run revision handled requests 2 minutes after gcloud reported the new one at 100 % (26 Aug 2026, live drill #2)
- **Symptom:** a job launched right after the deploy carried the old startup script; the fix from #29 looked ineffective.
- **Cause:** request log shows `/jobs/launch` at 18:13:19 served by revision 00017 although 00018 was "deployed and serving 100 %" at 18:11.
- **Fix:** `/healthz` returns `K_REVISION`; the live drill (and any deploy gate) waits until the served revision equals `latestReadyRevisionName`.

## #31 · Preemption detected as "stopped externally" (26 Aug 2026, live drill #2)
- **Symptom:** two real Spot preemptions in 4 minutes (us-central1-a) opened a `stopped_external` incident; after the restart was preempted again the ladder had nothing left and escalated — the `preempted` ladder would have offered `relocate_zone`.
- **Cause:** `zoneOperations.list` with `targetLink : "<name>"` returned no operations; the substring filter does not match on that field.
- **Fix:** filter by `operationType` only and match the target suffix in code; verified against the real operations of that VM (two events found).

## #32 · Diagnosed before the evidence landed (26 Aug 2026, live drill #3)
- **Symptom:** a textbook CUDA-OOM run (`exit=1`, traceback in the log) was diagnosed `unknown` → notify → **RESOLVED**; nothing was done.
- **Cause:** the agent posted `RUN_FIN` 8 s after the run ended; the pipeline diagnosed on the same tick with an empty log tail (the per-run log was still being uploaded by `wrun`). The Investigator even wrote "log sync delay" as hypothesis #2 — and then wandered through other jobs' logs (21 tool calls, $0.27). A notify with an empty ladder was treated as resolution.
- **Fix:** (1) `wrun` uploads the run log and artifacts **before** `RUN_FIN.json` becomes visible; (2) the pipeline reads the run's own log and defers up to 4 minutes while it is empty and the marker is fresh; (3) a notification on a critical / `unknown` / needs-human incident **escalates** instead of resolving.

## #33 · Every log read from Storage failed silently (26 Aug 2026, live drill #4)
- **Symptom:** the run log was in Storage (1,094 B, OOM traceback visible with `gcloud storage cat`), yet Diagnostician and Investigator both saw `total_lines: 0`; health `gcs` had 25 consecutive failures that nobody looked at.
- **Cause:** `Blob.download_as_text(errors="ignore")` — the installed google-cloud-storage rejects the keyword (`TypeError`); the exception was caught and turned into a health record and an empty log.
- **Fix:** `download_as_bytes().decode("utf-8", errors="ignore")` everywhere; unit test with a stub blob; **operator rule**: a red `gcs`/`gemini`/`memory` health row is a failed drill, not a footnote — the live drill script now prints the health table at the start.

## #34 · `/healthz` is answered by Google's frontend with a 404, never by the container (26 Aug 2026)
- **Symptom:** `GET /healthz` on the Cloud Run URL returns Google's HTML 404 (also with an identity token), while `/health` returns the JSON. The live drill's revision wait parsed the 404 as "" and treated it as ready — a silent false pass.
- **Fix:** use `/health`; an unreadable health response is "not ready", never "ready".

## #35 · Two nightly Scheduler jobs had been failing 401 since they were created (27 Aug 2026)
- **Symptom:** `warden-gold-eval` (02:00 WIB) and `warden-soak` (02:30 WIB) reported `lastAttemptTime` every night and were `ENABLED`, so both looked healthy in `gcloud scheduler jobs list`. In fact every attempt since 25 Aug returned **401 UNAUTHENTICATED**: the gold evaluation had never run automatically (its 11/11 result came from a manual run on 26 Aug) and no `eval/soak-*` document was ever written by the scheduler.
- **Cause:** both jobs targeted the service's *old* hostname form (`https://warden-core-hfgre6y7ta-uc.a.run.app`) and therefore minted OIDC tokens with that audience, while the container verifies `audience == WARDEN_SELF_URL` (`https://warden-core-603873318528.us-central1.run.app`). On mismatch `_oidc_ok` falls back to verifying without an audience and then requires the caller to be an owner email — a Scheduler service account never is, so it returns False. `warden-tick`, `warden-steward` and `warden-digest` were created with the project-number hostname and were unaffected; `warden-deadman` performs no application-level check, so its old-hostname target still worked.
- **Fix:** both jobs re-pointed to the project-number hostname with a matching `--oidc-token-audience`. Proven live: a real `jobs run warden-soak` returned **HTTP 200** (Cloud Run log 13:59:58Z) where the same job logged 401 at 19:30Z the night before.
- **Cost:** $0 in money; two nights of the automated evaluation and soak gate were missing. Both checklist items (C4 "nightly", H5 "soak") had been claimed on evidence from manual runs.
- **Operator rule:** a Scheduler job's `ENABLED` state and `lastAttemptTime` prove only that it *fired*. The gate is the target's HTTP status (`severity>=ERROR` on `resource.type="cloud_scheduler_job"`), and — for any job that is supposed to write something — the artefact it should have written.

## #36 · The nightly gold evaluation could never have run: three layers, one symptom (27 Aug 2026)
Found by fixing #35 — the 401 had been hiding everything behind it.
- **Layer 1 — wrong directory.** `warden/eval/gold.py` read the gold set from `tests/fixtures/gold`, and `.gcloudignore` keeps `tests/` out of the Cloud Run image. The first authenticated `/eval` died with `FileNotFoundError` in 0.14 s. The gold set is production data for a nightly job, not a test fixture, so it moved into the package: `warden/eval/cases/`.
- **Layer 2 — no trace.** The crash produced only an HTTP 500. Nothing was written to `health`, nobody was notified; had it happened at 02:00 it would have been invisible, exactly like #33. `/eval` now records `health gold_eval = False` with the exception text and notifies **before** re-raising.
- **Layer 3 — the blanket `*.log` rule.** After the move and a deploy (rev 00022), the scheduled run *still* failed: `cases.yaml` shipped but all eleven case logs were dropped by `*.log` in `.gcloudignore`. `gcloud meta list-files-for-upload .` listed 1 of 12 gold files. Fixed with a re-include (`!warden/eval/cases/*.log`) → 12 of 12.
- **The guard test was too weak, twice.** The first version checked that the *directory* was inside the package — layer 3 sailed through it. `tests/test_deployable_assets.py` now evaluates **every** gold file against `.gcloudignore` with gcloud's semantics (last matching pattern wins, `!` re-includes); the offline matcher is cross-checked against `gcloud meta list-files-for-upload` (114 files, zero disagreement) and was verified to fail on the exact file production crashed on.
- **Proof:** rev 00023-pl5, `warden-gold-eval` triggered through Cloud Scheduler → `eval/2026-08-27T144330Z`: **11/11, accuracy 1.0, 0 fabricated, $0.0695**, health `gold_eval` green. First time the nightly leg has ever run.
- **Cost:** $0 for the failed attempts (they crashed before reaching Gemini) + $0.07 for the successful one.
- **Operator rule:** "the file is in the repo" is not "the file is in the image". For anything the deployed service reads at runtime, the gate is `gcloud meta list-files-for-upload`, and a test that asserts it.

## #37 · The gold set was never in the repository (27 Aug 2026)
- **Symptom:** a clean clone of `main` failed two tests that are green in the working tree: `git clone` → venv → `pytest` → 2 failed, 81 passed. Found by running the N2 gate, which exists for exactly this.
- **Cause:** `.gitignore` carries a blanket `*.log`. The eleven real failure logs the Diagnostician is evaluated against have always ended in `.log`, so they were never committed — not under `warden/eval/cases/`, and not under `tests/fixtures/gold` before that. `git mv` moved the directory on disk and updated the index only for the one tracked file (`cases.yaml`), without a word about the rest. The gold set existed on exactly one machine; C4's "11/11 from real logs" was reproducible nowhere.
- **Why it hid so long:** `gcloud run deploy --source .` uploads the *working tree*, not the git checkout, so production had the files (once `.gcloudignore` allowed them — #36) even though the repository never did. Local pytest passed for the same reason.
- **Fix:** `.gitignore` re-includes `warden/eval/cases/*.log`; the eleven files are committed. The guard test now requires every gold file to be **tracked by git**, not merely present on disk.
- **Proof:** clean clone of the fixing commit → 12 gold files present, **84 passed**, chaos 25/25, 77.6 s from `git clone` to green (2.2 s clone · 2.5 s venv · 41 s pip · 27 s pytest · 4.6 s chaos).
- **Cost:** $0. The damage was potential: the evaluation set was one disk failure away from gone, and no reviewer could have reproduced C4.
- **Family:** #36 and #37 are the same mistake in two ignore files — a blanket pattern written for *output* applied to *data*. Whenever an asset's extension matches an ignore rule, check both `.gitignore` and `.gcloudignore`, and prove it with `git ls-files` and `gcloud meta list-files-for-upload`.

## #38 · A live drill that raced a phase it could not see in time (27 Aug 2026, A4 attempt #1)
- **Symptom:** the A4 drill triggered a real Spot preemption "inside the eval phase" and then waited for a `preempted` incident that never came. Warden opened no incident at all.
- **Not a Warden defect — the opposite.** The evidence says the run had already finished: `RUN_FIN` exit 0 at 15:28:11 with `eval.jsonl` 10 rows and `pred.csv` 2001 rows in Storage, while GCE recorded the preemption at 15:28:47 — **36 s after the job was done**. The `preempted` rule is TERMINATED ∧ no RUN_FIN, so opening nothing was correct. The job went COMPLETE on real, complete artifacts.
- **Cause (in the drill):** the trigger was a heartbeat, and a heartbeat is a *lagging* signal — the agent forwards it on its own loop. The "phase = eval" heartbeat reached Firestore at fold 9 of 10; the drill read it as "eval has just started" and raced a phase with 22 s left.
- **Fix:** the eval phase is now long enough to outlast the lag (10 folds × 45 s), the drill refuses to act on a heartbeat older than 90 s, the trainer reports the eval fold number so the remaining phase time is *computed* rather than assumed, and the gate asserts `RUN_FIN` does not exist both immediately before and after the preemption — a drill that interrupts nothing must fail loudly instead of passing quietly.
- **Cost:** ≈ $0.005 (one e2-medium Spot VM for ~9 min).
- **Operator rule:** never trigger a timed event off a lagging signal. Either wait for a signal that carries its own timestamp and check its age, or make the window wide enough that the lag cannot consume it — and always assert afterwards that the thing you meant to interrupt was actually still running.

## #39 · `gcloud run deploy warden-ui --source .` serves the *core* app (30 Aug 2026)
- **Symptom:** after deploying the dashboard, `GET /` on `warden-ui` returned `{"detail":"Not Found"}` and `/health` answered with the core's payload (`provider`, `project`). The dashboard was down for the minute between the deploy and the check.
- **Cause:** the repository ships four Procfiles — `Procfile` (core), `Procfile.core`, `Procfile.ui`, `Procfile.deadman` — and buildpacks read `Procfile`. `--source .` therefore builds every service with the **core** entrypoint. Nothing in the command names the service's real entrypoint, so the mistake is silent: the build succeeds, the revision goes healthy, and the health endpoint even answers 200.
- **Fix now:** deploy the UI with `Procfile.ui` in place (`cat Procfile.ui > Procfile`, deploy, restore), which is what earlier deploys did by hand.
- **Detection rule:** a Cloud Run deploy is not verified by a green revision or a 200 on `/health` — both services answer that. The gate is a response only that service can give: `curl -s $UI_URL/ | grep '<title>Overview · Warden'`.
- **Cost:** ~1 minute of dashboard downtime, caught immediately because the deploy was followed by opening the page rather than trusting the "Done."

## #40 · The tour tested a stale server and reported its failures as the product's (30 Aug 2026)
- **Symptom:** a tour run reported `/incidents` as HTTP 500 with `jinja2.exceptions.UndefinedError: 'f' is undefined`, on code that had just passed the lint, the screenshots and a direct functional check.
- **Cause:** `chaos/ui_tour.py` and `chaos/ui_serve.py` used the same ports (8099/18099). A long-lived `ui_serve` — started for the owner to browse, before the filter work — still held the port. The tour's own servers failed to bind, and Playwright then drove **that** process: an older `app.py` that does not pass the `f` context, rendering the **current** templates read fresh from disk, which expect it. The tour was measuring a server it did not start.
- **Why it was dangerous rather than merely annoying:** every result in that run was about the wrong process. A failing test that fails for an invented reason is worse than no test, because the next move is to "fix" code that was never broken.
- **Fix:** `ui_serve` runs on its own ports (8098/18098), both are overridable through `WARDEN_TOUR_UI_PORT` / `WARDEN_TOUR_CORE_PORT`, and `serve()` now refuses to start when the port already answers instead of quietly inheriting someone else's server.
- **Cost:** ~20 minutes and one wasted tour run. No production impact.
- **Operator rule:** a harness that starts its own server must prove the server it talks to is the one it started. Silent port reuse turns a test into a coin toss.

## #41 · Ask Warden never worked in production: a sync wrapper called from the event loop (30 Aug 2026)
- **Symptom:** the `gemini` health row went red with `asyncio.run() cannot be called from a running event loop`, three consecutive failures, while `last_ok_at` still read 25 Aug. Found while verifying a deploy — and the timing (07:22 WIB) matches the owner opening the production dashboard, so the first person to hit it was the owner.
- **Cause:** `/ask` is an `async def` endpoint, so FastAPI runs it **inside** the event loop. It called `concierge.ask()`, the synchronous face of `ask_async()`, which is `asyncio.run(...)` — illegal from a running loop. Every question asked through the dashboard raised, was caught, and wrote a red health row. The sibling endpoints that use the LLM (`/tick`, `/eval`) are plain `def`, so FastAPI runs them in a worker thread where `asyncio.run` is fine; only `/ask` sat on the wrong side of that line.
- **Why it hid:** the failure is invisible unless somebody asks a question. Nothing in the drills, the gold evaluation or the chaos suite calls `/ask`, and the health row was the only witness — a red row nobody read, which is the same shape as catalogue #33 and #35.
- **Fix:** the endpoint awaits `ask_async` directly. `tests/test_ask_endpoint.py` asserts it, and — the general rule — that **no** async endpoint calls one of the sync agent wrappers; verified to fail when the fix is reverted.
- **Cost:** $0. Ask Warden, one of the three checklist items under K3, has never answered a question in production.

## #42 · A read-only tool raised instead of answering, and the request hung (30 Aug 2026)
- **Symptom:** with #41 fixed, `/ask` still failed — `400 Document parent name ".../documents/runs/" has invalid trailing "/"` — and the HTTP request then hung until the 180 s client timeout.
- **Cause:** the question was fleet-wide ("which jobs needed a human?") and named no job, so the model called `get_heartbeats(job_id="")`. That reached Firestore as the document path `runs/`, which is invalid. The tool raised, the raise propagated out of the agent turn, and the run never completed. Worse, the agent had **no way** to answer such a question at all: every tool required a `job_id`, and nothing listed the jobs.
- **Fix:** every job-scoped tool now returns `{"error": ..., "jobs": [...]}` for a missing or unknown job instead of raising — an error a model can act on, with the job ids it needs — and two discovery tools were added, `list_jobs` and `list_incidents(state=…)`, so a question that names no job has a first move.
- **Test:** `tests/test_investigator_tools.py` calls every job-scoped tool with an empty, blank and unknown job id and requires a dict with an error and the job list.
- **Cost:** $0 in money; one Gemini call wasted, and Ask Warden still could not answer a fleet-wide question.
- **Operator rule:** a tool handed to an agent is an API for something that will pass it wrong arguments. It must fail like an API — a value the caller can read — not like a library.

## #43 · Health rows that only the failure path ever wrote (30 Aug 2026)
- **Symptom:** after `/ask` was fixed and answered correctly, the `gemini` row stayed red. After the Gemini breaker closed, `llm_circuit` stayed red. Both described a state that had already ended.
- **Cause:** each row was written only when something went wrong. `db.health(source, ok)` resets the failure streak on a `True`, but nothing ever passed `True` on those two paths, so the first bad minute pinned the row red for good.
- **Why it matters more than it looks:** a row that no success can clear is a row people stop reading, which is exactly how catalogue #33 (25 consecutive silent GCS failures) and #35 (401 every night) stayed hidden. The rule "success must leave a trace" exists for the same reason.
- **Fix:** `/ask` records `gemini` healthy on the way out, and `process_diagnosing` records `llm_circuit` healthy whenever it runs with the breaker closed. `tests/test_llm_circuit.py` opens the breaker with five failures and then closes it with one success.
- **Proof:** all 11 health rows green after the next tick.
