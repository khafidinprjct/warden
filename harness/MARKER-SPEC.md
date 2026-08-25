# Kontrak harness Warden (v1)

Semua berkas di `/var/lib/warden/<job>/` di mesin; `warden-agent` mengirimnya ke Warden (HMAC) dan menyalin log ke GCS.

| Berkas | Penulis | Isi wajib |
|---|---|---|
| `train.json` | `warden_beat.beat()` dari loop training (≤120 s / 50 step) | `phase, step, epoch, loss, lr, grad_norm, step_per_s, last_ckpt` |
| `markers/RUN_START.json` | `wrun` | `job_id, run_id, phase, ts, boot_id, cmd` |
| `markers/RUN_FIN.json` | `wrun` (dari exit code PROSES ANAK) | `exit_code, run_id, ts, boot_id, artifacts[{path,bytes,sha256}], evidence{rows,metrics}, signature=HMAC(job|run|exit|ts)` |
| `markers/PHASE_<nama>_{start,end}.json` | job (wajib untuk fase >15 mnt) | `ts, step, ckpt` |
| `markers/SMOKE_FIN.json` | smoke job | `members[], n_forward, loss_finite` |
| `markers/PREFLIGHT_FAIL.json` | `install.sh` | `reason` |
| `markers/VERIFIED.json` | **hanya Warden** (setelah membuka artefak) | `artifacts[], checks[]` |
| `artifacts/` | job (tulis `.tmp` → rename) | + sidecar `.sha256` dibuat `wrun` |
| `evidence.json` | job (opsional) | `{rows:{file:n}, metrics:{...}}` → masuk RUN_FIN.evidence |

Aturan: marker tanpa `exit_code`/`signature`/`run_id` **tidak diterima** sebagai selesai. `DONE` teks biasa = `DONE_LEGACY` (ditolak, mode #5).
Mode legacy (job tanpa `beat()`): agent tetap mengirim denyut host; Warden mem-parse `run.log` (`=== [Fx] ===`, `EXIT=n`, `loss`, `step`) menjadi denyut sintetis berpenalti confidence.
Mailbox: agent mem-poll `GET /cmd/<job>` (HMAC) → `{cmd: kill|resume|quarantine|collect_diag|inject, args}`; sekali pakai.
Preempt: agent membaca metadata `/instance/preempted` tiap siklus → `SIGUSR1` ke proses job → job harus flush checkpoint ≤30 s.
