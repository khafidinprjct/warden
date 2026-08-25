# Runbook Warden

## Rutin harian
- Baca digest Discord 08:00 WIB (biaya, ETTR, insiden). Tidak ada digest = periksa `/health` (watcher/steward basi?) dan Scheduler.
- Dashboard `/incidents`: yang `AWAITING_APPROVAL` → putuskan (kedaluwarsa 30 mnt → ESCALATED). `ESCALATED` → baca bukti, bertindak manual, lalu tutup.

## Insiden umum
| Gejala | Langkah |
|---|---|
| Kartu `preempted` tapi mesin tidak hidup lagi | lihat keputusan: DENY (stok/batas) → `gcloud compute instances start` manual atau pindah zona; FAILED → baca `result.error` |
| `artifact_unverified` | artefak sudah dikarantina di mesin (`/var/lib/warden/<job>/quarantine`); putuskan rerun (`resume`) atau rollback (`rollback_last_good`) dari kartu |
| `stuck` | Diagnostician memberi kategori; kalau `unknown` → `/warden why <job>`, lihat log di GCS `jobs/<job>/log/tail.log` |
| Warden sendiri diam (alarm email "mati senyap") | Cloud Run `warden-core` → log; Scheduler `warden-tick` state; deadman akan STOP mesin managed setelah 15 mnt |
| Budget 80 % | mesin demo di-STOP otomatis, LLM turun ke lite; 100 % → semua STOP + baca-saja; lepas manual: `policies/runtime.read_only=false` |
| Salah tindakan otomatis | `/warden freeze` (tombol merah), lalu `/warden hold <job> 2h`; turunkan tingkat di `policies.yaml` → deploy |

## Perintah operator
`python -m warden.cli job add|list|show · tick · steward · freeze on|off · approve|deny <id> · ettr <job>`
Discord: `/warden freeze|thaw|hold <job> <jam>|status|why <job>`.

## Memasang di mesin yang sudah ada
`sudo WARDEN_JOB=<id> WARDEN_CORE_URL=<url> WARDEN_HMAC=<rahasia> WARDEN_BUCKET=<bucket> WARDEN_ENTRY=<substr entrypoint> WARDEN_RESUME_CMD='<perintah ulang>' bash harness/install.sh`, lalu jalankan job lewat `wrun --job <id> -- <perintah>`; daftarkan job: `python -m warden.cli job add ...`.
Rahasia HMAC: Secret Manager `warden-ingest-hmac`.

## Rotasi & kebersihan
- Kunci SA dev lokal (`~/.config/warden/sa-core.json`) hanya untuk pengembangan; hapus dengan `gcloud iam service-accounts keys delete` bila tidak dipakai.
- Emulator lokal: `make emulators`; data uji di database `warden-test`/`warden-chaos`.
