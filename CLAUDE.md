# KARTU MASUK — Warden (All Things Agentic Hackathon, Devpost/Google Cloud) — sesi khusus proyek ini
Dimuat otomatis ke setiap agen yang bekerja dari folder ini. Aturan di bawah MENGIKAT (turunan `/home/ubuntu/lintasai/CLAUDE.md`, aturan induk
proyek pemilik) + status terakhir. Sesi ini HANYA mengurus Warden. Lomba lain (Lost in Transcription di `/home/ubuntu/lomba-transkripsi`,
Barbados di `/home/ubuntu/barbados`) tidak disentuh dari sini.

## 0. Baca DULU, sebelum bertindak
1. `docs/CEKLIS-WARDEN.md` — status resmi 61 butir A–N (✅/◐/☐) dengan bukti tiap butir.
2. `docs/JURNAL-KEPUTUSAN.md` — kronologi; entri terakhir **FREEZE 26 Agu pagi** (dibekukan atas perintah pemilik).
3. `docs/FAILURE-CATALOG.md` (36 cacat nyata yang ditemukan drill/gerbang hidup), `docs/RUNBOOK.md`, `docs/SUBMISSION.md`, `docs/SECURITY-REVIEW.md`, `docs/OBSERVABILITY.md`, `docs/DEMO.md`.
4. Rencana induk (Fase 0–14, prinsip P1–P14, katalog 25 mode kegagalan): `/home/ubuntu/.claude/plans/witty-jingling-whisper.md`.

## 1. Fakta hackathon
- Devpost "All Things Agentic"; tenggat **31 Agu 2026 17:00 PDT** (= 1 Sep 07:00 WIB). Rubrik 40/30/30, syarat wajib + bonus: lihat memori `hackathon-devpost-rubrik` / `docs/SUBMISSION.md`.
- Rumah: akun inyongkhafid@gmail.com, project GCP `warden-260825-a1446f`; Cloud Run `warden-core` (rev ≥00021), `warden-ui` (00010), `warden-deadman`; Scheduler tick 1 mnt, steward 10 mnt, digest, gold-eval 02:00 WIB, soak 02:30 WIB. Repo GitHub `khafidinprjct/warden` (pushed).
- Kredit GCP $150 → hemat: uji pada komponen NYATA (Firestore prod, VM asli) tapi STOP setelah uji, deploy per batch. Model Gemini: jangan "lite" untuk penalaran agen.

## 2. Status terakhir (27 Agu 2026 ~21:45 WIB, AKTIF — beku dibuka)
- Ceklis **68 butir** (angka "61" yang lama basi): ✅ 62 · ◐ 5 (A4 fase eval live; H5 soak s/d 1 Sep — 27 Agu: 26 insiden/12 tindakan/**0 palsu**; J4 ekspor billing = pemilik; K1 audit desktop = pemilik; N2 waktu deploy-ke-hidup) · ☐ 1 (K4 Discord + video — TERAKHIR).
- **27 Agu:** katalog #35 (gold-eval & soak 401 tiap malam sejak lahir — audience OIDC menunjuk nama host lama) + #36 (set emas tak pernah sampai ke image; TIGA lapis: salah direktori → crash tanpa jejak → aturan `*.log`) ditemukan & ditutup. Core **rev 00023-pl5**. Nightly gold eval berjalan untuk pertama kalinya lewat Scheduler: **11/11, accuracy 1,0, 0 palsu, $0,0695**. pytest 83, chaos 25/25.
- Bukti kunci: drill hidup #5 LULUS PENUH (`chaos/live_lifecycle_report.json`: spec→VM→OOM→resume batch 0,5→verifikasi→COMPLETE→laporan→stop); IAM condition I2 terbukti; gold eval 11/11 (terjadwal); pytest 83; chaos 25/25.
- Pending pemilik: hapus/tidak 9 disk VM drill (≈$14/bulan); ekspor billing; audit desktop UI; Discord creds + video di akhir.
- Alat: `python -m chaos.live_lifecycle` (≈$0,02), `infra/iam_condition.sh apply|test`, `python -m warden.eval.gold`, `python -m chaos.soak --days 7`, `infra/billing_reconcile.py`, `Makefile`, `Procfile*`.
- Jebakan: (1) revisi Cloud Run lama masih melayani ±2 mnt setelah "deployed" → cek `/health` field `revision` (`/healthz` dijawab 404 oleh frontend Google); (2) `pgrep/pkill -f` pola yang muncul di perintah sendiri → exit 144 — pakai `[c]haos` dan jangan sebut string yang sama di perintah lain; (3) health row merah (gcs/gemini) = drill tidak sah; (4) us-central1-a badai preempt spot 25–26 Agu; (5) Scheduler `ENABLED` + `lastAttemptTime` hanya berarti job MENYALA — gerbangnya status HTTP target (`severity>=ERROR` pada `resource.type="cloud_scheduler_job"`) + artefak yang seharusnya ia tulis (katalog #35); (6) aset yang dibaca layanan saat runtime: "ada di repo" ≠ "ada di image" — gerbangnya `gcloud meta list-files-for-upload` (katalog #36).

## 3. Arah produk (keputusan pemilik, mengikat)
- Warden = agen SRE sungguhan untuk pekerjaan komputasi panjang: mengurus GPU/CPU/disk/biaya/artefak — BUKAN mengurus token API dirinya sendiri. Tanpa batas LLM/tool buatan sendiri: default framework.
- Google Cloud saja; kanal manusia Discord + dashboard NiceGUI; otonomi bertahap per jenis tindakan (L0–L3); LLM tidak memegang tombol (keputusan deterministik); STOP bukan DELETE; tidak menyebut merek lain; UI bahasa Inggris istilah SRE standar.
- Demo/video/Discord = langkah PALING AKHIR, setelah Warden selesai; jangan dimasukkan ke pertimbangan sebelum itu.

## 4. Aturan mengikat (ringkas dari induk)
1. **Jurnal tanpa diperintah** → `docs/JURNAL-KEPUTUSAN.md` (keputusan · alasan+bukti · alternatif ditolak · biaya/risiko), lalu commit + push.
2. **Lapor kegagalan saat terjadi + harganya**. 3. **Nol klaim tanpa bukti** (FAKTA/HIPOTESIS/TIDAK-TAHU; tiap butir ceklis wajib bukti tes/live).
4. **Ukur dulu, baru bangun**; ukur kasus terburuk; setiap diagnosis punya pembanding; artefak dibuktikan dengan membukanya.
5. **Compute**: izin pemilik sebelum memakai GPU/VM (+taksiran biaya); spot STOP bukan DELETE; `--no-boot-disk-auto-delete`; ledger dulu, mesin kemudian; verifikasi per sumber daya (jangan pangkas stderr).
6. **Tanpa fallback senyap**; gagal-nyaring lalu diperbaiki. 7. **Kerjakan yang diminta saja**; menyimpang dari rencana induk hanya dengan izin eksplisit.
8. **Commit setiap perubahan** (repo di-push). 9. Pemantau wajib untuk proses latar; marker bawa bukti; keberhasilan harus meninggalkan jejak (denyut).
10. Higiene konteks: operasi akun/mesin lewat skrip bernama; jangan tampilkan kredensial; sesi padat → sesi baru.
11. Bahasa Indonesia informal, ringkas, bukti yang bisa diperiksa; UI/dokumen produk dalam bahasa Inggris standar. Waktu untuk pemilik dalam WIB.
12. Tutup setiap laporan dengan: posisi sekarang · langkah berikutnya · kapan pemilik perlu turun tangan.
