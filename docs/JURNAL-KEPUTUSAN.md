# JURNAL KEPUTUSAN — Warden

Format tiap entri: tanggal · keputusan · alasan+bukti · alternatif ditolak · biaya/risiko.

## 25 Agu 2026 — Proyek dimulai (Fase 0)
- Rencana induk: `plan.md` rev-3 (15 fase 0–14, fokus GCP saja). Semua keputusan arsitektur ada di sana.
- Keputusan pemilik: GCP saja; Discord + NiceGUI; otonomi bertahap; akun inyongkhafid, project baru; tanpa merek lain.
- Lingkungan: venv python3.12 di `.venv` (google-adk 2.7.1, google-genai, google-cloud-{compute,storage,firestore,pubsub,secret-manager,logging,monitoring}, fastapi, nicegui, PyNaCl, torch CPU menyusul saat Fase 5). Java 21 + emulator Firestore/Pub/Sub dipasang untuk pengembangan tanpa billing.
- Blocker luar: billing account GCP belum aktif; API key AI Studio belum ada; bot Discord belum ada — semua di tangan pemilik.
- Biaya sejauh ini: $0.
