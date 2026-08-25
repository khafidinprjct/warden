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
