"""Daftarkan slash command /warden ke aplikasi Discord (sekali). Butuh env WARDEN_DISCORD_APP_ID + WARDEN_DISCORD_BOT_TOKEN."""
import os, httpx
app_id, tok = os.environ["WARDEN_DISCORD_APP_ID"], os.environ["WARDEN_DISCORD_BOT_TOKEN"]
cmd = {"name": "warden", "description": "Warden controls", "options": [
    {"type": 1, "name": "status", "description": "digest: cost / ETTR / incidents"},
    {"type": 1, "name": "freeze", "description": "red button: freeze every autonomous action"},
    {"type": 1, "name": "thaw", "description": "lift the freeze"},
    {"type": 1, "name": "hold", "description": "hold actions on one job", "options": [{"type": 3, "name": "job", "description": "job id", "required": True}, {"type": 10, "name": "hours", "description": "duration (hours)", "required": False}]},
    {"type": 1, "name": "why", "description": "diagnosis of the job's latest incident", "options": [{"type": 3, "name": "job", "description": "job id", "required": True}]},
    {"type": 1, "name": "ask", "description": "ask Warden anything; attach a photo of a screen if you have one", "options": [
        {"type": 3, "name": "question", "description": "your question", "required": True},
        {"type": 11, "name": "image", "description": "a photo or screenshot to read", "required": False},
        {"type": 3, "name": "job", "description": "narrow it to one job", "required": False}]}]}
r = httpx.put(f"https://discord.com/api/v10/applications/{app_id}/commands", headers={"Authorization": f"Bot {tok}"}, json=[cmd], timeout=20)
print(r.status_code, r.text[:200])
