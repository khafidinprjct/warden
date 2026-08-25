# Tinjauan keamanan (Fase 12) — 25 Agu 2026 17:20 WIB
Perintah: `make audit` (bandit + pip-audit). Hasil pertama:

| Alat | Cakupan | Hasil | Tindak lanjut |
|---|---|---|---|
| `pip-audit -r requirements.txt` | 100 % dependensi terpasang | **0 kerentanan diketahui** | ulang tiap deploy |
| `bandit -r warden` | kode layanan | HIGH 0 · **MEDIUM 1** · LOW 11 | lihat bawah |
| `bandit harness/` | agen di mesin (stdlib) | MEDIUM 3 (B310 urlopen) · LOW ~12 | diterima, alasan di bawah |

**MEDIUM yang diterima dengan alasan:**
- `warden/ui/dashboard.py:240` B104 bind `0.0.0.0` — wajib di Cloud Run (port dari `$PORT`, jaringan dibatasi oleh Cloud Run ingress). Bukan permukaan serangan tambahan.
- `harness/warden-agent.py:42/54/113` B310 `urlopen` — URL tetap (`http://metadata.google.internal/...` untuk tanda preempt/identitas, dan `WARDEN_CORE_URL` https dari env root-only `/etc/warden/agent.env`), bukan input pengguna.

**LOW:** `try/except/pass` di agen = disengaja (agen tidak boleh mati karena satu sensor gagal; kegagalan terlihat sebagai denyut yang tidak bertambah — prinsip P4). `subprocess` tanpa shell dengan argumen list = benar.

**Batas yang diketahui (belum ditutup):** dashboard `warden-ui` belum di balik IAP/OAuth (hanya rahasia sesi + URL tidak dipublikasikan); rotasi HMAC manual; dead-letter Pub/Sub belum dipasang. Ini sisa Fase 12.

## Pembaruan Fase 12 — 25 Agu 2026 21:40 WIB
| Kontrol | Status | Bukti |
|---|---|---|
| Endpoint push Pub/Sub (`/events`, `/budget`) | OIDC wajib (SA `warden-scheduler`, audience = URL core) | `warden/main.py`; langganan `billing-alerts-push`, `warden-events-push` dengan `--push-auth-service-account` |
| Dead-letter | `warden-dead-letter` (5 percobaan), langganan inspeksi 7 hari, alert Monitoring `warden dead-letter messages` → email | `gcloud pubsub subscriptions list`; policy 9957601327191765404 |
| Rotasi HMAC harness | tanpa downtime: core menerima secret aktif **atau** sebelumnya (`WARDEN_INGEST_HMAC_SECRET_PREV`) selama masa tenggang; `infra/rotate_hmac.py` (versi baru → core → metadata VM → `--finish`) | `warden/signals/ingest.py::verify` |
| Notifikasi gagal-aman | Discord/Firestore notifikasi gagal → tindakan tetap jalan, dicatat `health/notify`, `health/discord` | `tick._safe_notify`, `main._notify`; uji `test_infra_chaos.py` |
| Gemini tidak tersedia | 5× gagal → circuit OPEN 5 mnt, insiden ESCALATED ke manusia (deterministik saja) | `test_infra_chaos.py::test_gemini_failures_open_circuit…` |
| Firestore lambat | tick tetap selesai < 10 s dan menulis denyut | `test_infra_chaos.py::test_slow_firestore…` |
| Kanal Slack | dihapus (keputusan pemilik) | `warden/concierge/slack.py` dihapus |
| **Login dashboard (IAP)** | **BELUM** — Cloud Run IAP butuh layar persetujuan OAuth; API pembuatan brand sudah dimatikan Google (Mar 2026) dan project tanpa organisasi → hanya bisa lewat Console oleh pemilik. IAP sempat diaktifkan → 502 untuk semua → dikembalikan `--no-iap`. Sampai IAP aktif, URL dashboard = rahasia bersama (risiko: siapa pun yang tahu URL bisa Approve/Freeze). | percobaan 25 Agu 21:20; binding `roles/iap.httpsResourceAccessor` untuk pemilik sudah dipasang |

## Update 26 Aug 2026 — checklist D/E/A (recovery executors, lifecycle)
- **Mailbox is signed.** Commands to the harness are Firestore documents signed by warden-core (HMAC-SHA256 over cmd/args/decision_id/ts/nonce with the ingest secret). The harness verifies before executing and reports the outcome (`/ingest/cmd_result`, HMAC). A document written by anything else is rejected and the rejection is itself reported. Threat closed: Firestore write access ≠ command execution on machines.
- **No delete, still.** New permissions on `wardenInstanceOperator`: instances.create/setServiceAccount/setTags, disks.create/createSnapshot/resize/use, snapshots.create/get/useReadOnly, subnetworks.use/useExternalIp, images.useReadOnly. No `*.delete`. Relocation keeps the source instance STOPPED and the snapshot; `clean_disk` deletes *files* only, and only those whose md5 equals the object in Storage.
- **Operator requests are not a side door.** `/jobs/{id}/propose` builds a Decision through the same policy engine (levels, limits, breaker, freeze, hold) and the same approval flow; executed actions are verified like any other.
- **Launch metadata** carries the ingest secret to the machine (as before via `vm_create.sh`); metadata is readable by anyone with `compute.instances.get` on the project. Mitigation unchanged: rotation with grace (`infra/rotate_hmac.py`); planned: Secret Manager access from the VM service account instead of metadata.
- **Harness kills by process group** with SIGTERM then SIGKILL after 20 s; only processes matching the job entrypoint (`WARDEN_ENTRY`) are targeted.
- **Verification deadlines** are policy (`recovery.verify_deadline_minutes`); an action that never confirms escalates instead of being assumed done.
- Residual: IAM condition on the core SA (name prefix `warden-`) is the next item (I2).
