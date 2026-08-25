# Status gerbang Fase 0–14 (diperbarui 25 Agu 2026 15:10 WIB)
Sumber bukti: `docs/JURNAL-KEPUTUSAN.md`, log `chaos/live_report.json`, `data/live_toy.log`, Firestore proyek `warden-260825-a1446f`.

| Fase | Gerbang | Status | Bukti |
|---|---|---|---|
| 0 Prasyarat | billing hidup, API aktif, venv, repo | ✅ | project `warden-260825-a1446f`, billing 01A7C4…, budget warden-150 (25/50/80/100 %) |
| 1 Kerangka | `make smoke` hijau, Gemini JSON lolos cek silang | ✅ | diagnosis NaN log nyata, $0,0107 |
| 2 Harness+Watcher | denyut ≥1/30 s dari mesin nyata, preempt → insiden <2 tick | ✅ | demo-train-1/2 heartbeat 30 s; `stopped_external` terbuka pada tick berikutnya |
| 3 Executor+kebijakan | preempt nyata → START otomatis (L2) → RUNNING; delete ditolak semua jalur | ✅ | chaos.live langkah 3b–3d: rugi 348 s; `Action` tanpa DELETE; 41 tes |
| 4 Diagnostician | ≥12 kasus, nol bukti palsu, ≤$0,03/insiden | ✅ | cek silang `evidence_lines` wajib ada; biaya tercatat di ledger LLM |
| 5 Verifier | CSV terpotong/NaN + exit 0 → DITOLAK; artefak asli VERIFIED | ✅ | pred.csv 1030 baris sha 810a76ac… VERIFIED; chaos s21 (tenggang 10 mnt → FINISHED_UNVERIFIED) |
| 6 Steward/deadman/kill-switch | yatim → STOP; core mati → deadman STOP ≤20 mnt; budget → reaksi | ✅ | endpoint `/steward` `/budget` diuji; warden-deadman SA sendiri |
| 7 Concierge Discord | kartu <5 s, Approve → tindakan → kartu diperbarui | ⏳ **butuh user** | kode + tes Ed25519 lulus; menunggu public key/bot token/channel id → Secret Manager, lalu `infra/discord_register.py` |
| 8 Dashboard NiceGUI | audit user lulus; websocket ≥10 mnt; cold start <8 s | ◐ **diimplementasikan 25 Agu 19:10** (English, timeline berlabel, kartu izin + Re-evaluate, STALE, HP 390); uji fungsional `chaos/ui_live.py` ✓; live ui-00004 / core-00011; **menunggu audit pemilik di HP** |
| 9 Multimodal | 5/6 gambar benar, ≤$0,005/gambar | ✅ | kurva loss dibaca Gemini (`concierge/images.py`) |
| 10 Chaos & latihan | 25/25 deterministik; suntikan nyata | ✅ | `chaos/run.py` 25/25; chaos.live 4/4 (climate-demo); toy-train preempt jujur → ckpt terpotong → pulih → COMPLETE+VERIFIED 18:1x (3 cacat nyata ditemukan & ditutup, katalog #26–#28) |
| 11 Dokumen/video/submisi | mesin bersih ≤30 mnt; video; Devpost | ◐ | mesin bersih **34 s** (tes+chaos); diagram PNG; **video butuh user** (atau headless) |
| 12 Pengerasan | rate limit, circuit breaker Gemini/provider, IAM | ◐ | rate limit ingest + circuit breaker Gemini ada; IAP/OAuth dashboard belum |
| 13 Observability | metrik+alert core basi; adapter Slack | ◐ | metrik `warden_heartbeat` + alert absence → email; adapter Slack ada, belum end-to-end |
| 14 Operasi berkelanjutan | 2 job berbeda dipantau 7 hari | ◐ | 2 job (climate-demo, toy-train) dipantau sejak 25 Agu; 7 hari belum genap |

**Uji preempt jujur toy-train:** ✅ lulus 25 Agu 18:1x — lihat jurnal 18:30 (rantai bukti) — setelah START disetujui pemilik.

**Yang hanya bisa dilakukan user:** kredensial Discord (Fase 7), audit UI (Fase 8), rekam video (Fase 11), memilih 2 submisi final Zindi.
