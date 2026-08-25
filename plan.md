# WARDEN — Rencana Fase 0 → Akhir (versi Fable, ditulis dari konteks kita)
25 Agu 2026 · rev-3 (fokus GCP saja; 15 fase: 0–14; +7 kemampuan dari riset §1b) · dokumen diskusi/audit · belum ada kode

---

## 0. Apa yang sedang kita bangun, dan untuk siapa

Warden adalah penjaga untuk **pekerjaan komputasi panjang** (training, evaluasi, pipeline) yang berjalan di mesin sewaan. Pengguna pertamanya kita sendiri — Chimera, DaT, Barbados, Climate — dan itu bukan kebetulan: setiap fitur di dokumen ini lahir dari kejadian yang pernah kita bayar. Kalau Warden tidak berguna untuk armada kita sendiri, ia tidak berguna untuk siapa pun.

Dua kalimat yang jadi tulang punggung produk:
- **Mesin hidup ≠ training benar.** Status RUNNING tidak berarti apa-apa. Yang penting: apakah step bertambah, loss masuk akal, disk cukup, proses cuma satu, dan kalau berhenti — apakah ada yang tahu dalam 5 menit.
- **Selesai ≠ utuh.** Marker DONE, exit code 0, file yang "ada" dengan ukuran yang benar — semuanya pernah berbohong pada kita. Yang dipercaya hanya artefak yang *dibuka*.

Hackathon Google (tenggat 1 Sep 07:00 WIB) adalah **potret** dari fase yang sudah utuh, bukan tujuan. Kalau pada tanggal itu yang utuh baru Fase 4, yang disubmit Fase 4 — tanpa memaksakan fitur setengah jadi.

### Keputusan yang sudah kamu ambil (mengikat)
**Google Cloud saja** (keputusan 25 Agu siang: fokus GCP; provider lain tidak dirancang) · kanal manusia **Discord** + dashboard **NiceGUI** · otonomi **bertahap per jenis tindakan** · rumah: akun **inyongkhafid@gmail.com**, **project baru** · kredit $150 ditebus ke billing yang hidup · **tidak menyebut merek lain** · UI/UX diaudit kamu · istilah teknis Inggris.

### Fakta lingkungan (diverifikasi 25 Agu)
- **Tidak ada billing account yang aktif** (tiga-tiganya tertutup). Tanpa ini Cloud Run/Firestore/Pub/Sub tidak bisa dinyalakan. Kode kredit sudah kamu terima; tidak kusimpan di mana pun.
- Lokal: gcloud 573, python3.12, node/bun, **tanpa docker** (deploy pakai `gcloud run deploy --source`), 29 GB disk sisa, RAM 23 GB. Java belum ada (emulator Firestore/Pub/Sub butuh Java 21). `/home/ubuntu/warden` = venv 3.10 kosong, dibuang.
- Semua skrip cloud kita memanggil `gcloud`/`gsutil` lewat subprocess. Warden akan memakai client library Google Cloud (alasan di §3.4).
- Model Gemini yang sah untuk lomba (wajib 3.5+): `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.7-flash`. Tidak ada 3.5 Pro. (Harga & nama diverifikasi ulang di Fase 1 sebelum dipakai.)
- ADK Python 2.7.x: agen LLM dengan skema keluaran JSON, agen loop, agen kustom deterministik, plugin callback. Satu hal yang **tidak** kuandalkan: mekanisme "minta konfirmasi" bawaan ADK — ia tidak bertahan lintas proses, sedangkan persetujuan manusia bisa datang 30 menit kemudian dari HP. Persetujuan akan hidup di Firestore, bukan di sesi LLM.

---

## 1. Prinsip — masing-masing punya luka asalnya

| # | Prinsip | Luka asal (bukti di repo kita) |
|---|---|---|
| P1 | **LLM tidak memegang tombol.** Gemini hanya menghasilkan diagnosis JSON + rekomendasi dari daftar tindakan tetap. Yang mengeksekusi: kode kebijakan deterministik. | Salah diagnosis OOM → patch salah sasaran → GPU terbakar (memori `baca-jejak-sebelum-menambal`) |
| P2 | **Deterministik dulu.** Status, mtime, ukuran, exit code, checksum, jumlah proses = kode biasa. LLM hanya untuk teks log yang tak bisa diregex aman, transient-vs-permanen, dan bicara ke manusia. | — |
| P3 | **Bukti = membuka.** Checkpoint dipercaya setelah `torch.load`; CSV setelah dibaca; salinan setelah checksum. Ukuran identik ≠ isi identik. | Checkpoint korup berukuran persis sama dengan yang sehat; checkpoint 15 % karena disk penuh |
| P4 | **Sukses harus berjejak.** Setiap loop menulis denyut juga saat semuanya baik. Sistem yang diam saat sukses tak bisa dibedakan dari sistem yang mati. | Penjaga cron mati 33× tanpa ketahuan (PATH cron) |
| P5 | **Warden diawasi pihak luar.** Ada alarm di luar Warden yang berbunyi kalau Warden berhenti berdenyut, dan bisa mematikan mesin sendiri. | Sama dengan P4 |
| P6 | **Dua syarat untuk alarm** macet/idle/yatim: satu sinyal diam (log basi/status) + satu sinyal aktivitas (CPU/GPU/mtime). | `verda_watch.sh` — alarm dua syarat sudah terbukti anti-false-positive |
| P7 | **Ledger dulu, mesin kemudian.** Catat instance ke buku besar SEBELUM setup panjang; sweeper yatim tidak bergantung pada proses yang bisa dibunuh. | Dua instance yatim 2 j 50 m, $1,3, ketahuan dari saldo |
| P8 | **STOP, jangan DELETE.** Delete tidak pernah otomatis, dan tidak ada di daftar tindakan yang bisa disetujui lewat Warden. | 75 klip hilang bersama disk |
| P9 | **flock, bukan pgrep.** Anti-duplikat proses lewat kunci file yang dilepas kernel. | Startup-script memblokir dirinya sendiri; proses ganda OOM |
| P10 | **Verifikasi per sumber daya.** Diminta 4, jadi berapa? Jangan pangkas stderr; baca error dari respons terstruktur. | 3 dari 4 `create` gagal, dilaporkan "armada meluncur" |
| P11 | **Resume sadar fase.** Kerugian preempt ≤ 5 menit untuk SEMUA fase (training, eval, panen). | 7,5 jam eval hangus ($15) karena resume hanya menutup training |
| P12 | **Ukur dulu, baru bangun.** Tiap fase punya uji termurah yang bisa membatalkannya; smoke wajib memuat komponen sebenarnya. | Smoke `--fast` lolos, produksi meledak NaN di member yang tak ada di smoke |
| P13 | **Jangan balapan dengan manusia.** Kalau operator sedang di mesin, Warden menahan diri. | Babysitter menyalakan VM yang sengaja dimatikan |
| P14 | **Lapor kegagalan saat terjadi, berikut harganya.** Setiap kartu insiden membawa biaya yang sudah dan akan terbakar. | Hukum SERAH-TERIMA §2 |

### 1b. Tujuh kemampuan tambahan dari riset (25 Agu; kerangka agen-operasi Google SRE + praktik reliabilitas training skala besar)
| # | Kemampuan | Dipasang di | Bentuk konkret |
|---|---|---|---|
| R1 | **`dry_run`** untuk setiap tindakan yang mengubah keadaan | Fase 3 | tiap aksi di registry menerima `dry_run=True` → mengembalikan rencana (target, operasi API, biaya, blast radius) tanpa memanggil API; dipakai kartu Discord ("ini yang akan terjadi") dan uji |
| R2 | **Tombol merah global** | Fase 7–8 | `/warden freeze` di Discord + tombol di dashboard → semua tindakan ke L0 seketika, eksekusi yang sedang berjalan dihentikan di titik aman, kartu "DIBEKUKAN oleh <siapa>"; `/warden thaw` untuk melepas |
| R3 | **Blast radius eksplisit** | Fase 3 | tiap `decision` membawa `blast_radius` {none, this_run, this_job, budget, artifacts} + jumlah mesin/artefak tersentuh; tampil di kartu & audit; kebijakan boleh menolak berdasarkan ini |
| R4 | **Postmortem otomatis** | Fase 7 | insiden RESOLVED/ESCALATED → ringkasan 10 baris (gejala, bukti, diagnosis, tindakan, hasil, biaya, pelajaran) ke `docs/POSTMORTEM.md` + thread Discord; bahan memori insiden |
| R5 | **`grad_norm`** di kontrak heartbeat | Fase 2 | field opsional di `warden.beat()`; lonjakan grad-norm = peringatan dini sebelum NaN |
| R6 | **ETTR** (Effective Training Time Ratio = waktu training efektif ÷ waktu mesin dibayar) | Fase 6 & 8 | dihitung per job dari denyut & ledger; tampil di Budget + digest harian; **KPI utama kebergunaan Warden** — ETTR sebelum vs sesudah Warden adalah angka jualan yang jujur |
| R7 | **Gold set + evaluasi malam** | Fase 10 | tiga tingkat log uji: bronze (sintetis), silver (log nyata berlabel Claude), **gold (log nyata yang kamu verifikasi)**; evaluasi otomatis tiap malam, skor di bawah ambang → kartu Discord |
Yang sengaja **tidak** diambil: uji bitwise per mesin untuk silent data corruption hardware — butuh armada besar, di luar cakupan kita (dicatat sebagai batasan di README).

---

## 2. Katalog 25 mode kegagalan → apa yang harus bisa dilakukan Warden
(semua pernah terjadi pada kita; harga = yang benar-benar dibayar)

| # | Mode | Harga | Warden harus |
|---|---|---|---|
| 1 | Spot dicabut, tidak ada yang menyalakan | 4 jam diam | deteksi ≤ 2 menit, nyalakan, resume |
| 2 | Spot dicabut + disk ikut terhapus | 75 klip | tolak konfigurasi `auto-delete`/`DELETE` sebelum mesin dibuat |
| 3 | Dicabut saat eval, resume hanya training | $15 / 7,5 jam | resume sadar fase |
| 4 | Penjaga mati senyap (PATH cron) | 33 jendela tanpa proteksi | denyut sukses + pengawas luar |
| 5 | DONE palsu (echo tanpa exit code) padahal OOM-killed | prep hilang | marker wajib bawa exit code + bukti; ukuran vs ekspektasi |
| 6 | DONE basi / res=0 | ~$0,10 + 40 mnt | marker terikat run-id + waktu; bukti angka |
| 7 | Checkpoint korup, ukuran identik | nyaris kehilangan model | buka + checksum |
| 8 | Disk penuh → checkpoint 15 % | nyaris resume dari file rusak | gerbang disk preventif; resume hanya dari yang terverifikasi |
| 9 | OOM di kasus terburuk | ~$1,15 | smoke kasus terburuk; klasifikasi OOM |
| 10 | Salah diagnosis OOM | GPU terbakar | diagnosis harus bisa dibantah oleh angka |
| 11 | pip gagal senyap, startup tetap DONE | ~$0,15 + 1 jam | gerbang dependensi keras; exit code dari proses anak |
| 12 | Image tanpa pip / .so rusak | run gagal | preflight sebelum kerja mahal |
| 13 | Fallback kernel senyap (eval 90 mnt) | jam GPU | throughput vs baseline |
| 14 | Instance yatim | $1,3 | ledger vs kenyataan, sapu |
| 15 | VM idle lupa dimatikan | $0,73 + $1,3 | idle dua-syarat → stop |
| 16 | Create gagal per-item, stderr dipangkas | jadwal + kepercayaan | verifikasi per item dari respons terstruktur |
| 17–18 | Kuota global vs regional; kuota disk | pengajuan sia-sia; 15 mnt | cek kuota sebelum meluncurkan |
| 19 | Stockout spot / badai preempt 5–12× | restart budget | backoff, rotasi zona, jalan keluar on-demand |
| 20 | Proses ganda (pgrep longgar) | risiko besar | flock |
| 21 | Selesai tapi artefak tertahan gerbang | run ulang | artefak wajib mendarat, gerbang hanya memberi rekomendasi |
| 22 | Smoke lolos palsu | ~$0,10 + 20 mnt | smoke mendeklarasikan komponen yang dimuat |
| 23 | Smoke menimpa artefak juara | nyaris kehilangan juara | jalur tulis terpisah + pantau path tak-boleh-berubah |
| 24 | `nohup &` via ssh menggantung | instance tanpa pemilik | tidak ada ssh di jalur kritis |
| 25 | Babysitter balapan dengan operator | VM harus dibuat ulang | mode manual + kunci |

---

## 3. Bentuk sistem

### 3.1 Gambar besar
```
                     ┌──────────────── Google Cloud (project baru "warden-…") ───────────────┐
  VM Compute Engine  │  Cloud Run  warden-core  (satu proses, lima loop, tanpa websocket)   │
  (spot/on-demand)   │   ┌ ingest   : terima denyut & marker dari harness (HMAC)            │
                     │   ├ watcher  : tiap 2 mnt, aturan deterministik → insiden            │
   ┌──────────┐      │   ├ pipeline : bukti → Gemini diagnosis JSON → cek silang → kebijakan │
   │ harness  │──POST─►   ├ executor : tindakan lewat provider, audit sebelum & sesudah      │
   │ (bash+py │◄─GET──│   └ steward  : ledger, idle, yatim, proyeksi biaya, sapu             │
   │ stdlib)  │ cmd   │  Cloud Run  warden-ui   (NiceGUI, websocket, dipisah agar tagihan   │
   └──────────┘       │                          & kegagalan UI tidak menyentuh core)       │
     │ log/artefak    │  Cloud Run  warden-deadman (service account SENDIRI: kalau core      │
     ▼                │                          berhenti berdenyut 15 mnt → STOP semua VM) │
   GCS bucket        │  Firestore (kebenaran) · Pub/Sub (antrian) · Scheduler (jam)         │
                     │  Secret Manager · Cloud Logging/Monitoring · Billing Budget          │
                     └───────────────────────────────┬────────────────────────────────────────┘
                                                     ▼
                                       Discord (kartu + tombol) ◄─► kamu di HP
```
Tiga layanan, bukan satu: **core** (tindakan; request-based, murah), **ui** (websocket; boleh mati tanpa mengganggu tindakan), **deadman** (pengawas luar, identitas terpisah — P5). Ini keputusan struktural, bukan kosmetik: tindakan tidak boleh bergantung pada UI, dan pengawas tidak boleh berbagi nasib dengan yang diawasi.

### 3.2 Lima loop di core (semua deterministik kecuali satu)
1. **Ingest** — menerima `hb` (denyut) dan `marker` dari harness; menyimpan; tidak berpikir.
2. **Watcher** (tiap 2 menit) — membaca status provider + denyut + marker + GCS; menjalankan aturan §4; membuka insiden dengan kunci dedupe; menulis denyut sendiri.
3. **Pipeline insiden** — satu-satunya tempat LLM: kumpulkan bukti → Gemini menghasilkan `Diagnosis` JSON → **cek silang deterministik** (klaim OOM harus cocok regex OOM / VRAM ≥ 95 %; klaim NaN harus cocok log/heartbeat; `evidence_lines` harus benar-benar ada) → kebijakan memutuskan AUTO / MINTA-IZIN / TOLAK.
4. **Executor** — mengambil kunci (lease) per job, menulis audit *niat*, bertindak lewat provider, menunggu operasi selesai, **membandingkan diminta-vs-jadi**, menulis audit *hasil*. Kalau hasil ≠ niat → insiden baru, bukan diam.
5. **Steward** (tiap 10 menit) — buku besar biaya (tarif × umur) per instance, deteksi idle/yatim dua-syarat, proyeksi runway, sapu, digest harian ke Discord (denyut ke manusia).

### 3.3 Di mana ADK & Gemini dipakai — jujur, bukan tempelan
- **Diagnostician**: agen LLM dengan skema keluaran JSON tetap (`category` dari 15 kategori, `confidence`, `evidence_lines`, `transient|permanent`, `recommended_action` dari 7 aksi, `needs_human`, `falsifiable_check` = "kalau benar, setelah tindakan X angka Y berubah"). Model 3.5 Flash. Konteks: 200 baris log terakhir (+60 sebelum Traceback), 10 denyut terakhir + baseline, marker, ringkasan 5 insiden terakhir job itu. ≤ 3 panggilan per insiden.
- **Investigator** (loop ≤ 3 putaran): boleh meminta jendela log lain, statistik artefak, kurva loss sebagai gambar — semua tool **hanya-baca**.
- **Vonis kedua** 3.7 Flash hanya bila confidence < 0,7 atau tindakan menyentuh lebih dari satu run; beda pendapat → manusia.
- **Concierge**: menjawab pertanyaan di Discord (`/warden why <job>`), membaca foto layar dari HP (OCR → temuan berlabel "dari foto, confidence ≤ 0,6", tidak pernah memicu aksi otomatis).
- **Tidak dipanggil** untuk preempt murni, idle, yatim, marker tidak sah, disk penuh — semuanya sudah pasti tanpa LLM. Biaya sasaran ≈ $0,03 per insiden; batas $2/hari.

### 3.4 Lapisan Compute Engine
Satu modul `gce.py` dengan fungsi jelas: `list_instances, describe, start, stop, relocate, stock_check, quota, price, send_command`. Lewat **client library** (bukan subprocess gcloud): container Cloud Run tidak punya gcloud; identitas dari service account; error per resource terstruktur — menutup mode #16; kuota global vs regional terbaca terpisah — menutup #17–18; `operations.wait` memberi diminta-vs-jadi — P10. `gcloud` hanya hidup di skrip setup/deploy. Untuk tes dan latihan tanpa biaya: `fake_gce.py` meniru API yang sama (preempt, stockout, kuota). Tidak ada abstraksi multi-cloud — kalau suatu hari dibutuhkan, `gce.py` sudah berbentuk satu modul yang bisa ditiru.

### 3.5 Kebenaran di Firestore
`fleet` (mesin), `jobs` (pekerjaan: fase, step, checkpoint terverifikasi terakhir, ekspektasi artefak, budget), `incidents` (mesin status: DETECTED → DIAGNOSED → DECIDED → {EXECUTING → VERIFYING → RESOLVED | AWAITING_APPROVAL | HELD | ESCALATED}), `decisions` (tiap tindakan + siapa menyetujui + kedaluwarsa), `evidence`, `policies`, `audit` (hanya-tambah), `costs`, `health` (per sumber sinyal), `leases`. Sesi LLM sengaja ephemeral; kalau Warden mati di tengah, semua bisa dilanjutkan dari Firestore.

---

## 4. Kontrak harness (sisi mesin) — jantungnya

Ini pengganti empat generasi babysitter kita, ditulis sekali dengan benar. Bash + Python stdlib, **tanpa pip** (mode #11–12), dipasang oleh satu skrip, mengawasi job yang sudah ada dengan mengganti satu baris peluncuran.

- **`wrun <job> -- <perintah asli>`** — `flock -n` (P9); `set -o pipefail`; tee log; saat proses anak selesai menulis `RUN_FIN.json` berisi `exit_code` **dari proses anak**, `run_id`, `boot_id`, `phase_last`, daftar artefak `{path, bytes, sha256}`, angka bukti `{rows, metrics}`, dan tanda tangan HMAC. Marker tanpa ini **tidak diterima** (mode #5, #6, #11).
- **`warden-agent`** (systemd, `Restart=always`) — tiap 30–60 detik POST denyut host: cpu, gpu util/mem, df, daftar proses entrypoint (path penuh + ppid), mtime log, file yang sedang ditulis, sesi ssh interaktif aktif (P13), tanda preempt dari metadata server; mengunggah potongan log ke GCS; mem-poll `cmd/` (kill, restart, run_verify, collect_diag) — tanpa ssh (mode #24).
- **`warden.beat()`** satu file — dipanggil dari loop training tiap 50 step / ≤ 120 detik: `phase, step, epoch, loss, lr, step_per_s, vram, last_ckpt`. Untuk job lama yang tak bisa diubah: **mode legacy** — parser log (`step`, `loss`, `=== [F2] … ===`, `EXIT=n`, `*_FIN`) membuat denyut sintetis; semua temuan legacy diberi penalti confidence → tindakan destruktif selalu minta manusia.
- **Marker fase** `PHASE_<nama>_{start,end}` wajib untuk fase > 15 menit; `startup.sh` melanjutkan dari fase terakhir, bukan dari awal (P11).
- **Artefak**: tulis ke `.tmp` → fsync → rename; sidecar `.sha256` + `.meta.json {step, expect_size}`; `VERIFIED.json` **hanya ditulis oleh Warden** setelah dibuka (P3).
- **Preflight** saat boot: df ≥ ambang, import torch/cuda, pip ada, `.so` kunci terbaca — gagal → marker `PREFLIGHT_FAIL` + berhenti (mode #12), bukan lanjut diam-diam.
- **Tanda preempt** (metadata `/instance/preempted`) → `SIGUSR1` ke trainer → checkpoint darurat ≤ 30 detik — inilah yang membuat "kerugian ≤ 5 menit" mungkin.
- **Smoke** menulis `SMOKE_FIN.json {members[], n_forward, loss_finite}`; Warden menolak smoke yang tidak memuat komponen yang dideklarasikan (mode #22). Smoke menulis ke direktori terpisah; path `champion/**` dipantau checksum-nya (mode #23).

---

## 5. Detektor & kebijakan

### 5.1 Aturan Watcher (deterministik) — urutan pembangunan mengikuti frekuensi × harga
1. **VM mati** (TERMINATED 2 tick ∧ tanpa RUN_FIN) + **badai preempt** (≥ 3/60 mnt → backoff 0/2/5/10/20 mnt, rotasi zona, k ≥ 4 → on-demand bila budget mengizinkan). Mode #1, #19.
2. **Warden sendiri** — denyut tiap tick; `warden-deadman` di luar. Mode #4.
3. **Marker sah** — tanpa exit code/tanda tangan/run-id = ditolak; `rows=0` saat ekspektasi > 0 = `marker_empty`. Mode #5, #6, #11.
4. **Artefak** — verifier torch/csv/jsonl/npz/parquet + ukuran vs ekspektasi (dipelajari dari artefak terverifikasi job itu, ±10 %) + checksum + "hanya ukur saat penulis diam" + karantina partial. Resume memilih **VERIFIED terakhir**, bukan `ls -t`. Mode #7, #8, #21.
5. **Yatim & idle** — mesin RUNNING tanpa job aktif di ledger ≥ 10 mnt ∧ tanpa denyut → yatim; job selesai ∧ cpu < 10 % ∧ gpu < 5 % ≥ 15 mnt → idle; grace 10 mnt setelah boot. Mode #14, #15.
6. **Macet dua-syarat** — denyut basi > 3×p95 interval ∧ (gpu < 5 % ∨ cpu < 10 %); basi tapi GPU sibuk = "lambat", bukan macet. Mode #13, P6.
7. **Proses ganda** — > 1 proses entrypoint (path penuh, worker dikecualikan via ppid). Mode #20.
8. **Fase** — resume sadar fase. Mode #3.
9. **Log parser + Diagnostician** — regex pasti (OOM, Killed, ImportError, PEP668, nan/inf, ENOSPC, Traceback) → kandidat; ambigu → LLM; klaim OOM harus bisa dibantah angka `Tried to allocate`. Mode #9, #10.
10. **Peluncuran & kuota** — tiap `create` diikuti `operations.wait` + `get` + denyut pertama ≤ 5 mnt; kuota global/regional/disk dicek sebelum. Mode #16–18.
Sisanya (#2 konfigurasi mesin, #12 preflight, #22 smoke, #23 immutable, #25 kunci) kecil setelah landasan ada.

### 5.2 Otonomi bertahap
Empat tingkat per **jenis tindakan**: L0 amati · L1 usulkan (kartu Discord, kedaluwarsa 30 mnt → eskalasi) · L2 lakukan lalu lapor · L3 lakukan diam (muncul di digest). Naik tingkat = keputusanmu, ditawarkan setelah 10 persetujuan berturut tanpa rollback.

| Tindakan | Awal | Batas |
|---|---|---|
| notify | L3 | digabung bila > 20/jam |
| start mesin setelah preempt | L2 | stok ada; ≤ 3/jam, 8/hari per job; biaya ≤ 1 jam tarif |
| resume job | L2 | ≤ 3/jam; hanya dari checkpoint VERIFIED; wajib lease |
| stop mesin idle/yatim/over-budget | L2 | grace 15 mnt + dua tick; 1/jam per mesin |
| karantina artefak (rename) | L2 | — |
| rollback ke checkpoint baik terakhir | L1 | 2/hari |
| pindah zona / ganti tipe mesin / resize disk | L1 | ≤ +50 % tarif |
| kill proses | L1 | via mailbox |
| **delete mesin/disk** | **tidak ada** | hanya manual di Console |

Pengaman lintas tindakan: **circuit breaker** per job (> 3 aksi otomatis/jam atau 2 verifikasi gagal berturut → semua turun ke L1 selama 60 mnt, kartu "Warden berhenti bertindak sendiri untuk job X karena …"); **pagu global** aksi otomatis $10/hari, LLM $2/hari; **mode manual** (`/warden hold <job> 2h`, sesi ssh terdeteksi → tunda 10–30 mnt, CLI `warden lease acquire` untuk skrip operator); **kunci** Firestore per job (TTL 5 mnt) agar dua tick tidak dobel; setiap keputusan membawa `explain` (aturan mana lolos/gagal) di kartu dan audit.

### 5.3 Pemulihan per kategori diagnosis
preempt → start → resume (badai: §5.1-1) · macet → kill + resume VERIFIED (3×/hari) · OOM GPU → resume batch ↓ (≤ 2×) + `expandable_segments`; angka tak berubah → manusia · OOM host → workers ↓ · NaN divergen → stop; tawarkan resume dari ckpt −2 + lr ↓ (izin) · NaN input / data / config / kode / env / dependensi → **stop mesin** + saran patch · fallback kernel → stop + laporan · disk rendah → hapus checkpoint lama non-VERIFIED yang sudah di GCS · jaringan → retry 3× · yatim/idle → notif → 15 mnt → stop · marker tak sah / belum terverifikasi → job tetap "berjalan", 30 mnt → manusia · budget 100 % → stop semua · tidak tahu → jaga mesin tetap ada, eskalasi.

---

## 6. Fase 0 → akhir
Tidak dijadwalkan per tanggal; tiap fase punya **gerbang keluar** (uji yang membuktikan) dan **uji pembatal** termurah (P12). Perkiraan jam = jam kerjaku. Hackathon = potret fase yang utuh saat 31 Agu.

### Fase 0 — Prasyarat (mulai hari ini)
**Kamu (±45 mnt, dari HP):** (1) Console → Billing sebagai inyongkhafid: reopen akun tertutup milikmu (butuh kartu valid) **atau** buat akun baru; (2) tebus kode kredit ke akun itu; verifikasi $150 muncul di Credits; (3) kirim Billing Account ID ke aku; (4) buat API key AI Studio (project apa saja selain warden) → simpan di `~/.config/warden/.env` (chmod 600, di luar repo); (5) buat server Discord pribadi + aplikasi bot (token + public key; endpoint diisi setelah deploy).
**Aku, tanpa billing (hari ini):** venv python3.12 di `/home/ubuntu/warden/.venv`; Java 21 + emulator Firestore/Pub/Sub; repo privat `khafidinprjct/warden` + `docs/JURNAL-KEPUTUSAN.md` + pre-commit penolak secret; commit pertama.
**Aku, setelah billing hidup (±30 mnt):** project baru, link billing, enable API (run, cloudbuild, artifactregistry, firestore, pubsub, secretmanager, compute, cloudscheduler, aiplatform, logging, monitoring, iam, billingbudgets), Firestore Native us-central1, topik `warden-events` + `billing-alerts`, **Budget $150 ambang 25/50/80/100 %** → Pub/Sub + email, 4 service account dengan peran minimal (core: instanceAdmin **dibatasi label `warden-managed`**, tanpa delete; vm: tulis GCS prefix job saja; scheduler: invoker; deadman: identitas sendiri), Secret Manager.
**Gerbang:** `billingEnabled: true`; API aktif; emulator hidup; `import google.adk` di venv 3.12; commit pertama. Biaya $0.

### Fase 1 — Kerangka yang memuat komponen asli (~6 jam)
Model domain + mesin status insiden + tes; akses Firestore/Pub/Sub via emulator; provider fake; skema `Diagnosis`; satu agen LLM ADK asli memanggil `gemini-3.5-flash` asli (AI Studio); `make smoke` menjalankan satu insiden palsu dari deteksi sampai keputusan.
**Uji pembatal (hari ini juga):** ADK 2.7 benar-benar mendukung skema keluaran JSON + agen loop + plugin seperti yang kuasumsikan. Kalau tidak, rancangan §3.3 disesuaikan sebelum ada kode lain.
**Gerbang:** `make smoke` hijau; dokumen `incidents/…` di emulator; JSON Gemini valid & lolos cek silang.

### Fase 2 — Harness + Watcher di mesin nyata (~8 jam) — butuh billing
`wrun`, `warden-agent`, `warden.beat` (termasuk `grad_norm`, R5), `startup.sh`, `install.sh`, `MARKER-SPEC.md`; aturan Watcher 1–3, 5 (§5.1); `warden-core` deploy pertama; skrip `vm_create.sh` (e2 spot, `termination-action=STOP`, `no-boot-disk-auto-delete`, label managed, SA vm) yang menjalankan **pipeline climate kita sungguhan** (`run_pipeline.py --fast`, clone commit tetap, tulis ke `submissions/smoke/`).
**Uji pembatal (dulu, lokal, gratis):** `/usr/bin/time -v run_pipeline.py --fast --jobs 2 --folds 2 --repeats 1 --optuna 0` → RSS & durasi → pilih e2-medium/e2-standard-2 sebelum mesin dibuat.
**Gerbang:** mesin menjalankan pipeline sampai `SELESAI` dengan denyut ≥ 1/30 detik di Firestore; `simulate-maintenance-event` → insiden preempt < 60 detik; denyut Watcher tercatat di `health`. Biaya ≈ $1.

### Fase 3 — Executor + kebijakan + audit (~6 jam)
Registry tindakan (§5.2) dengan **`dry_run`** di setiap aksi (R1) dan **`blast_radius`** eksplisit di setiap keputusan (R3), `policies.yaml`, `engine.evaluate()` murni dengan tes matriks 100 %, lease, circuit breaker, audit niat/hasil, diminta-vs-jadi.
**Gerbang:** preempt nyata → start otomatis (L2) → RUNNING terverifikasi → denyut BOOT; permintaan delete = ditolak di semua jalur; audit terbaca sebab-akibat; `dry_run` tiap aksi mengembalikan rencana identik dengan yang kemudian dieksekusi.

### Fase 4 — Diagnostician + cek silang (~6 jam)
Pengumpul bukti, Investigator, Diagnostician, cek silang deterministik, vonis kedua 3.7 Flash, prefilter Flash-Lite, plugin batas biaya; set evaluasi dari log nyata kita (`run_eks_gagal1.log` NaN, `log_penjaga.txt` wake-loop, smoke Chimera, log `[F2]..[F6]`).
**Gerbang:** ≥ 12 kasus, tindakan benar ≥ 90 %, **nol bukti palsu** (setiap `evidence_lines` benar-benar ada), biaya ≤ $0,03/insiden tercatat.

### Fase 5 — Verifier artefak (~5 jam)
Plugin csv (1030 baris, kolom, NaN, rentang, ID sejajar), json, parquet, jsonl, npz, torch (`weights_only`, kunci wajib, isfinite sampel, sha ≠ sebelumnya; berjalan di mesin lewat mailbox bila besar); ekspektasi ukuran dipelajari; "ukur saat penulis diam"; karantina; VERIFIED.
**Gerbang:** suntik CSV terpotong + NaN + `RUN_FIN exit 0` → **DITOLAK** dengan bukti baris; artefak asli diterima; `champion/**` tidak berubah.

### Fase 6 — Steward, sapu, deadman, kill-switch anggaran (~6 jam)
Ledger tarif × umur; **ETTR per job** (R6); idle/yatim dua-syarat; proyeksi runway; `warden-deadman`; handler budget: 50 % peringatan, 80 % stop mesin demo + turunkan model, 100 % stop semua + baca-saja.
**Gerbang:** mesin yatim buatan → terdeteksi + STOP dalam satu sapu + proyeksi biaya; matikan core (traffic 0) → deadman mematikan mesin ≤ 20 mnt; pesan budget palsu → reaksi sesuai ambang.

### Fase 7 — Concierge Discord (~6 jam)
Endpoint interaksi (verifikasi Ed25519, ack ≤ 3 detik, edit pesan lewat bot token); embed insiden (kelas, job, fase, denyut, diagnosis + confidence + vonis kedua, bukti ≤ 8 baris, usulan + risiko + biaya, hitung mundur); tombol Approve / Deny / Always-24h; klik ganda idempoten; thread hasil; foto layar dari HP; **`/warden freeze` / `thaw`** (R2, tombol merah global); **postmortem otomatis** per insiden selesai (R4); digest harian 08:00 WIB dengan ETTR.
**Gerbang:** dari HP: insiden → kartu < 5 detik → Approve → tindakan → kartu diperbarui dengan hasil + biaya; timeout teruji.

### Fase 8 — Dashboard NiceGUI (~8 jam) — kamu audit
Halaman: Fleet · Incidents · Incident detail (sparkline denyut, log bernomor dengan bukti disorot, JSON diagnosis + langkah Investigator, verdict verifier, blast radius, tombol) · Budget (dengan **ETTR**) · Policies · Audit · Health; **tombol merah FREEZE** selalu terlihat di header. Mobile-first 390 px; data dari Firestore listener; approve dari dashboard = hasil sama dengan Discord. Ceklis auditmu: tiap klaim ada bukti + biaya; tombol destruktif jelas; "basi" tak bisa disangka hidup; waktu WIB + relatif; state kosong/loading/error; tanpa merek lain.
**Gerbang:** auditmu lulus; websocket bertahan ≥ 10 mnt di Cloud Run; cold start < 8 detik.

### Fase 9 — Multimodal (~3 jam)
Warden merender kurva loss dari denyut (matplotlib) → Diagnostician menilai plateau/divergen bila numerik ambigu; foto layar HP → OCR → temuan berlabel.
**Gerbang:** 3 foto + 3 kurva → 5/6 benar; ≤ $0,005/gambar.

### Fase 10 — Uji kekacauan & latihan (~6 jam)
`chaos/` 25 skenario dengan `fake_gce` + trainer dummy (time-warp), 4 suntikan nyata (preempt, artefak korup, yatim, matikan Warden), `pytest -m live`; **set uji bronze/silver/gold** (gold = log nyata yang kamu verifikasi) + **evaluasi malam otomatis** dengan ambang (R7); **3 latihan demo penuh dengan stopwatch**.
**Gerbang:** 25/25 deterministik terdeteksi; 48 jam job sehat sintetis = 0 tindakan palsu; 3 latihan berturut < 3:40.

### Fase 11 — Dokumen, diagram, video, submisi (~10 jam)
README (masalah → diagram → spin-up 30 mnt → kontrak harness → kebijakan → keamanan → biaya → uji → batasan), diagram, video 4 menit satu take (tiga adegan: preempt → izin dari HP → hidup lagi; DONE palsu ditolak dengan bukti; mesin yatim dimatikan dengan proyeksi biaya; Console terlihat), subtitle Inggris sebagai CC, teks Devpost, repo dibagikan, `warden-ui` min-instances 1 selama masa juri. Bonus murah: post sosial (+0,2), blog dari jurnal (+0,2), Gemma 4 sebagai prefilter (+0,2, harus terlihat di video).
**Gerbang:** mesin bersih mengikuti README → dashboard hidup ≤ 30 mnt; ceklis submisi penuh.

**→ Potret hackathon = fase terakhir yang lulus gerbangnya pada 31 Agu 20:00 WIB.** Kalau tertinggal, urutan yang dilepas: foto HP → tombol Always → halaman Policies/Audit → bonus. Yang tidak dilepas: denyut, cek silang, verifier, deadman, izin Discord, tiga adegan.

### Fase 12 — Pengerasan industrial (~12 jam)
IAM condition diperketat; rate limit; circuit breaker untuk Gemini/provider (5× gagal → buka 5 mnt, model cadangan); retry/backoff; Pub/Sub dead-letter; rotasi secret; `bandit` + `pip-audit` di CI; kekacauan infra (Gemini 500, Firestore lambat, Discord mati).
**Gerbang:** semua kekacauan berdegradasi terkendali; tinjauan keamanan bersih.

### Fase 13 — Observability Warden (~6 jam)
Dashboard Monitoring (latensi tick, insiden/jam, biaya/insiden, error Gemini), SLO (deteksi < 60 detik, keputusan < 30 detik), alarm core basi. Kanal manusia tetap Discord + dashboard (keputusan pemilik 25 Agu: tidak ada Slack).
**Gerbang:** alarm palsu terkirim; SLO terukur 7 hari.

### Fase 14 — Operasi berkelanjutan
Registry multi-job (Chimera TTS dengan checkpoint torch asli sebagai job kedua, di Compute Engine), kebijakan per job, memori insiden lintas job, promosi otonomi dari rekam jejak, runbook, retro mingguan biaya vs ledger, regresi evaluasi mingguan.
**Gerbang:** dua job berbeda di Compute Engine dipantau 7 hari tanpa insiden terlewat; biaya bulanan ≤ proyeksi.

---

## 7. Anggaran $150
| Komponen | $/bulan |
|---|---|
| `warden-core` (request-based, min 0; tick 2 mnt) | 0–2 |
| `warden-ui` (min 0; kamu buka ±3 jam/hari) | 2–4 · +≈10 hanya saat masa juri (min 1) |
| `warden-deadman`, Firestore, Pub/Sub, Scheduler, Secret Manager, Logging | ≈ 0 (free tier) |
| Cloud Build + Artifact Registry (~40 deploy) | 0–3 |
| Gemini (≈ $0,03/insiden; dev 500 insiden, operasi 100/bln) | 3–15 |
| Mesin demo e2 spot + IP + disk (≈ 40 jam/bln) | 2–3 |
| **Total** Agu ≈ 10–15 · Sep ≈ 30–40 · Okt ≈ 15–20 → **≈ $55–75 / 3 bulan**, sisa ≈ $75 | |
Kill-switch: ledger internal memicu lebih dulu (data Billing tertunda berjam-jam); Budget resmi = jaring terakhir; pelepasan billing dari project tetap manual. Angka mesin demo diverifikasi dengan kalkulator harga di Fase 2.

## 8. Risiko & rencana B
| Risiko | Rencana B |
|---|---|
| Billing tak kunjung hidup | Fase 0–1 + sebagian 3–5 jalan di emulator + AI Studio; **emulator tidak sah untuk demo** (syarat lomba: backend di Google Cloud) → batas keras billing hidup ≤ 29 Agu pagi, kalau tidak potret hackathon = Fase 1 (tidak layak submit) — kita terima itu dan lanjut industrial |
| Mesin demo tak sanggup pipeline | diukur dulu (Fase 2); `--folds 2 --repeats 1 --optuna 0` atau tipe lebih besar |
| Simulasi preempt tidak memicu | fallback `stop`; Watcher menangani "dihentikan dari luar" |
| Websocket NiceGUI di Cloud Run | session affinity + timeout 60 mnt, 1 worker, state dari Firestore; fallback polling 5 detik |
| LLM salah diagnosis | cek silang + vonis kedua + tindakan hanya dari enum + verifikasi pasca-tindakan |
| Biaya Gemini melonjak | loop ≤ 3, ≤ 3 panggilan/insiden, $2/hari, log dipotong |
| Rekam video gagal | cadangan Playwright headless di server ini + CC Inggris |
| Jadwal molor | daftar lepas §6; potret hackathon = fase terakhir yang lulus |
| Kamu jadi bottleneck | 4 slot: hari ini (billing, key, Discord), saat audit UI (Fase 8, 30 mnt), rekam (2 jam) |

## 9. Definisi "selesai" untuk versi industrial
1. Warden menjaga **armada kita sendiri di Compute Engine** selama ≥ 30 hari dan tidak ada satu pun dari 25 mode yang lolos tanpa terdeteksi.
2. Nol tindakan destruktif otomatis; setiap tindakan punya audit niat + hasil + biaya.
3. Ledger cocok dengan tagihan ± 10 %; **ETTR** armada terukur dan naik dibanding sebelum Warden.
4. Kamu bisa menyetujui/menolak dari HP dalam satu ketukan, dan dashboard lulus auditmu.
5. Mesin bersih mengikuti README sampai hidup ≤ 30 menit.

## 10. Yang perlu kamu putuskan saat mengaudit
1. Nama & tagline final (tanpa merek lain).
2. Angka kebijakan awal (§5.2): $10/hari aksi otomatis, $2/hari LLM, idle 15 mnt, 3 start/jam — setuju atau ubah?
3. Track hackathon: Taskmaster (saranku) atau Enterprise Fleet.
4. Job demo = pipeline climate (`--fast`), job industrial kedua = Chimera TTS (checkpoint torch asli) — setuju?
5. Slot waktumu: billing hari ini; audit UI di Fase 8; rekam video 2 jam.
