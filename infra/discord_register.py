"""Daftarkan slash command /warden ke aplikasi Discord (sekali). Butuh env WARDEN_DISCORD_APP_ID + WARDEN_DISCORD_BOT_TOKEN."""
import os, httpx
app_id, tok = os.environ["WARDEN_DISCORD_APP_ID"], os.environ["WARDEN_DISCORD_BOT_TOKEN"]
cmd = {"name": "warden", "description": "Kendali Warden", "options": [
    {"type": 1, "name": "status", "description": "digest biaya/ETTR/insiden"},
    {"type": 1, "name": "freeze", "description": "tombol merah: bekukan semua tindakan otomatis"},
    {"type": 1, "name": "thaw", "description": "lepas pembekuan"},
    {"type": 1, "name": "hold", "description": "tahan tindakan pada satu job", "options": [{"type": 3, "name": "job", "description": "job id", "required": True}, {"type": 10, "name": "jam", "description": "lama (jam)", "required": False}]},
    {"type": 1, "name": "why", "description": "diagnosis insiden terakhir job", "options": [{"type": 3, "name": "job", "description": "job id", "required": True}]}]}
r = httpx.put(f"https://discord.com/api/v10/applications/{app_id}/commands", headers={"Authorization": f"Bot {tok}"}, json=[cmd], timeout=20)
print(r.status_code, r.text[:200])
