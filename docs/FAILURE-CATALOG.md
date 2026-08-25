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
