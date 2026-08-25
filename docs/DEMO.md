# Demo 4 menit — tiga adegan, satu take, Console terlihat

Persiapan 30 menit sebelum rekam: `demo-train-1` sedang menjalankan pipeline (fase F3); `demo-train-2` sudah selesai dengan artefak utuh; `stray-vm-7` (label managed, job sudah selesai) dibuat 20 menit sebelumnya. Tab: Console Compute Engine · Console Cloud Run · dashboard `warden-ui` · Discord.

| Detik | Layar | Aksi |
|---|---|---|
| 0:00–0:25 | dashboard Armada | dua kalimat inti; komponen di Cloud Run/Firestore/Pub/Sub/Gemini via ADK |
| 0:25–0:40 | Cloud Shell | `bash demo/1_preempt.sh` → `simulate-maintenance-event` (fallback `stop`) |
| 0:40–1:05 | Console → dashboard | Terminated; insiden `preempted` < 60 dtk; denyut putus di F3 |
| 1:05–1:25 | detail insiden | bukti, keputusan `start_instance` L2, dry-run, audit niat/hasil |
| 1:25–1:45 | Discord | kartu; (bila L1) tap Approve → "Executing…" → "RUNNING verified" |
| 1:45–2:00 | Console + dashboard | mesin Running, denyut BOOT kembali, resume sadar fase |
| 2:00–2:10 | Cloud Shell | `bash demo/2_corrupt.sh` → mailbox `inject corrupt_csv` |
| 2:10–2:45 | dashboard | RUN_FIN exit 0 → Verifier membuka CSV: "611/1030 baris, 3 NaN" → DITOLAK, karantina otomatis, job FINISHED_UNVERIFIED |
| 2:45–2:55 | Cloud Shell | `bash demo/3_sweep.sh` → `/steward` sekarang |
| 2:55–3:25 | Anggaran + Discord | `stray-vm-7` yatim → "kalau dibiarkan $X/bulan" → STOP (L2) |
| 3:25–3:50 | Console Cloud Run + Firestore | bukti backend: 3 layanan, log Vertex, koleksi `audit` + `costs` |
| 3:50–4:00 | dashboard | penutup: otonomi bertahap, FREEZE, ETTR |

Rekam dari HP (Chrome mode desktop) satu take; narasi Indonesia + subtitle Inggris sebagai track CC (video tak diedit).

## Cadangan headless (25 Agu 2026 17:50 WIB) — `docs/video/rekam_tur.py`
`docs/video/tour_cc.mp4` (2:47, CC terbakar) dan `tour.mp4` (+ track `tour.en.srt`): tur 9 halaman dashboard di atas data insiden nyata hari ini (preempt climate-demo → START L2 → resume; verifier membuka pred.csv; mesin yatim; Anggaran/ETTR; Kebijakan; Audit; Kesehatan). Ini **bukan** pengganti video submisi 3 adegan satu take (Console harus terlihat) — itu tetap direkam user, atau versi headless panjang setelah START toy-train disetujui.
Temuan audit UI dari bingkai: sparkline "Denyut" untuk job legacy (climate-demo, tanpa step/loss) tergambar datar 100/0,5 — menyesatkan; harus menampilkan cpu% atau "tidak ada step/loss (legacy)". Dicatat untuk Fase 8.
