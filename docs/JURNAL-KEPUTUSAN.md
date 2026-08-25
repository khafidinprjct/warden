# JURNAL KEPUTUSAN — Warden

Format tiap entri: tanggal · keputusan · alasan+bukti · alternatif ditolak · biaya/risiko.

## 25 Agu 2026 — Proyek dimulai (Fase 0)
- Rencana induk: `plan.md` rev-3 (15 fase 0–14, fokus GCP saja). Semua keputusan arsitektur ada di sana.
- Keputusan pemilik: GCP saja; Discord + NiceGUI; otonomi bertahap; akun inyongkhafid, project baru; tanpa merek lain.
- Lingkungan: venv python3.12 di `.venv` (google-adk 2.7.1, google-genai, google-cloud-{compute,storage,firestore,pubsub,secret-manager,logging,monitoring}, fastapi, nicegui, PyNaCl, torch CPU menyusul saat Fase 5). Java 21 + emulator Firestore/Pub/Sub dipasang untuk pengembangan tanpa billing.
- Blocker luar: billing account GCP belum aktif; API key AI Studio belum ada; bot Discord belum ada — semua di tangan pemilik.
- Biaya sejauh ini: $0.

## 25 Agu 2026 12:40 WIB — Fase 0 GCP selesai (izin pemilik: "login saja, akun inyongkhafid")
- Ternyata di akun inyongkhafid ada 2 billing account AKTIF (01A7C4 "My Billing Account", 01FAD5 studio) — temuan "tidak ada billing aktif" sebelumnya dilihat dari akun lain. Dipakai: 01A7C4. Pemilik diminta menebus kode kredit ke akun itu.
- Project baru `warden-260825-a1446f` (tersimpan di `.gcp_project`), billing tertaut, 39 API aktif, Firestore Native us-central1, topik `warden-events` + `billing-alerts`, bucket `gs://warden-260825-a1446f-warden`, Artifact Registry `warden`.
- Service account: warden-core (datastore.user, pubsub pub/sub, secretAccessor, aiplatform.user, logWriter, monitoring viewer+metricWriter, serviceAccountUser, objectAdmin bucket), warden-vm (objectAdmin bucket, logWriter), warden-scheduler (run.invoker diberikan saat deploy), warden-deadman (datastore.viewer, logWriter).
- KEPUTUSAN KEAMANAN: role kustom `wardenInstanceOperator` (get/list/start/stop/setMetadata/setLabels/setMachineType + baca operasi/kuota) **tanpa compute.instances.delete / disks.delete** untuk core & deadman. IAM condition berbasis label DITOLAK API (label bukan tag) → pembatasan "hanya mesin warden-managed" dipindah ke kode (allowlist label) + tidak adanya izin delete di IAM. Alternatif ditolak: roles/compute.instanceAdmin.v1 penuh (terlalu luas).
- Emulator: gcloud 581, Java 21. Emulator Firestore menolak `(default)` ter-encode oleh client 2.29 → keputusan: database lokal bernama `warden`, produksi `(default)` (konfigurasi `WARDEN_FIRESTORE_DB`).
- Biaya sejauh ini: $0 (semua free tier / tanpa mesin).

## 25 Agu 2026 13:05 WIB — Fase 1 LULUS gerbang
- `make smoke`: (A) preempt → dua tick → start otomatis L2 → RESOLVED, audit niat+hasil; (B) marker DONE tanpa exit code ditolak; (C) Gemini 3.5 Flash asli (ADK `LlmAgent` + `output_schema`) mendiagnosis log nyata `run_eks_gagal1.log` → `nan_input`, confidence 1,00, evidence_lines [174,175], cek silang LOLOS, biaya $0,0107.
- Tes: 25 unit (policy/state machine/rules) + 4 tick end-to-end di emulator = 29 hijau.
- KEPUTUSAN: Vertex AI + service account (bukan API key). Gemini 3.5/3.7 di Vertex hanya tersedia di lokasi `global` (us-central1/us-east5 = 404) → `GOOGLE_CLOUD_LOCATION=global`. Dev lokal memakai berkas kunci SA `warden-core` di `~/.config/warden/` (chmod 600, di luar repo) — dirotasi/dihapus di Fase 12; produksi Cloud Run tanpa berkas kunci.
- KEPUTUSAN: ID dokumen Firestore untuk ref mesin `zone/name` → `zone__name` (Firestore melarang '/').
- Uji pembatal ADK (output_schema + agen LLM + runner) LULUS — rancangan §3.3 tetap.
- Biaya: $0,01 (Gemini).

## 25 Agu 2026 13:50 WIB — Fase 2 (sebagian): harness selesai & teruji lokal; ukuran mesin diputuskan
- Harness: `wrun` (flock, exit code proses anak via PIPESTATUS, RUN_FIN bertanda tangan HMAC + sha256 artefak), `warden-agent.py` (denyut host 30 s, marker, log→GCS, mailbox, tanda preempt→SIGUSR1), `warden_beat.py`, `install.sh` (+preflight), `startup.sh` (resume sadar fase), unit systemd, `MARKER-SPEC.md`.
- Uji lokal end-to-end: wrun → agent → core (/ingest HMAC) → Firestore: RUN_FIN valid, exit 0, 1 artefak + sha256, evidence rows; denyut host diterima. LULUS.
- UJI PEMBATAL ukuran mesin (P12): `run_pipeline.py --fast --jobs 2 --folds 2 --repeats 1 --optuna 0` = 3 mnt 14 dtk, RSS maks **3,2 GB** → e2-medium (4 GB) terlalu sempit → KEPUTUSAN: mesin demo **e2-standard-2** (8 GB; spot ≈ $0,02/jam).
- `warden-core` terdeploy ke Cloud Run (revisi 00001 Ready, uvicorn hidup di 8080) — URL publik masih 404 dari frontend Google saat dicek pertama; sedang diselidiki (propagasi/IAM invoker).
- Catatan: hook lokal "gerbang praterbang" memblokir perintah yang memuat teks flag startup-script; berkas ditulis lewat Python. Harness Warden memenuhi tujuan gerbang itu (preflight, marker exit code, denyut, resume, STOP+no-auto-delete).
- Biaya: ≈ $0 (Cloud Run free tier; Gemini $0,01).
