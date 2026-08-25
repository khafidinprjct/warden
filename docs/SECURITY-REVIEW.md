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
