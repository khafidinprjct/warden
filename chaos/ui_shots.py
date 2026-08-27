"""Full-page screenshots of every dashboard page, desktop and phone, for a visual design review.

The tour proves the controls work; this is for judging how the interface reads. Same local stack, same seeded board.

    python -m chaos.ui_shots            # → docs/screenshots/ui2/<page>-{desktop,phone}.png
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("TOUR_OUT", ROOT / "docs/video/tour"))
SHOTS = Path(os.environ.get("SHOT_DIR", ROOT / "docs/screenshots/ui2"))
SHOTS.mkdir(parents=True, exist_ok=True)
CORE_PORT, UI_PORT = "18100", "8100"
os.environ.update({
    "FIRESTORE_EMULATOR_HOST": "127.0.0.1:8081", "WARDEN_PROJECT": "warden-local", "WARDEN_FIRESTORE_DB": "warden-tour",
    "WARDEN_PROVIDER": "fake", "WARDEN_DEV": "1", "WARDEN_FAKE_STATE": str(OUT / "fleet.json"),
    "WARDEN_CORE_URL": f"http://127.0.0.1:{CORE_PORT}", "PORT": UI_PORT,
})
UI = f"http://127.0.0.1:{UI_PORT}"

from warden.store import firestore as db  # noqa: E402


def main() -> int:
    inc = next((i for i in db.incidents.list(limit=400) if i.diagnosis), db.incidents.list(limit=1)[0])
    pages = [("/", "overview"), ("/incidents", "incidents"), (f"/incidents/{inc.incident_id}", "incident"),
             ("/approvals", "approvals"), ("/jobs", "jobs"), ("/jobs/vision-7b", "job"), ("/jobs/launch", "launch"),
             ("/fleet", "fleet"), ("/budget", "budget"), ("/policies", "policies"), ("/audit", "audit"),
             ("/system", "system"), ("/ask", "ask")]
    env = dict(os.environ)
    logs = open(OUT / "shots_servers.log", "w")
    core = subprocess.Popen([sys.executable, "-m", "uvicorn", "warden.main:app", "--port", CORE_PORT, "--log-level", "warning"],
                            env=env, stdout=logs, stderr=subprocess.STDOUT)
    ui = subprocess.Popen([sys.executable, "-m", "warden.ui2.app"], env=env, stdout=logs, stderr=subprocess.STDOUT)
    import httpx
    for _ in range(90):
        try:
            if httpx.get(f"{UI}/health", timeout=3).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            for tag, vp in (("desktop", {"width": 1440, "height": 900}), ("phone", {"width": 390, "height": 844})):
                ctx = b.new_context(viewport=vp, timezone_id="Asia/Jakarta", device_scale_factor=1)
                pg = ctx.new_page()
                for path, name in pages:
                    pg.goto(UI + path, wait_until="load", timeout=60000)
                    pg.wait_for_timeout(500)
                    pg.screenshot(path=str(SHOTS / f"{name}-{tag}.png"), full_page=True)
                    print(f"  {name}-{tag}.png", flush=True)
                ctx.close()
            b.close()
    finally:
        core.terminate(); ui.terminate()
    print("→", SHOTS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
