"""Functional test of the Jinja2 UI (warden.ui2) on the emulator + persistent fake fleet: pages render, Freeze/Thaw and Approve act, phone has no horizontal scroll."""
import os, subprocess, sys, time, json
from pathlib import Path
os.environ.update({"FIRESTORE_EMULATOR_HOST": "127.0.0.1:8081", "WARDEN_PROJECT": "warden-local", "WARDEN_FIRESTORE_DB": "warden-test", "WARDEN_PROVIDER": "fake", "WARDEN_DEV": "1",
                   "WARDEN_FAKE_STATE": "/tmp/claude-1001/-home-ubuntu-lintasai/8cd3c608-c7cb-4791-a09a-9f328214e737/scratchpad/fake_state2.json", "WARDEN_CORE_URL": "http://127.0.0.1:18096", "PORT": "8096"})
from chaos.ui_live import seed  # noqa: E402 — it rewrites env at import; ours is re-applied below
ENV = {"WARDEN_FAKE_STATE": "/tmp/claude-1001/-home-ubuntu-lintasai/8cd3c608-c7cb-4791-a09a-9f328214e737/scratchpad/fake_state2.json", "WARDEN_CORE_URL": "http://127.0.0.1:18096", "PORT": "8096"}
os.environ.update(ENV)
from warden.core.models import DecisionStatus
from warden.providers import registry
from warden.store import firestore as db

S = os.environ.get("SHOT_DIR", "docs/screenshots/ui2")
Path(S).mkdir(parents=True, exist_ok=True)
PAGES = [("/", "overview"), ("/incidents", "incidents"), ("/approvals", "approvals"), ("/jobs", "jobs"), ("/fleet", "fleet"), ("/budget", "budget"), ("/policies", "policies"), ("/audit", "audit"), ("/system", "system")]


def main():
    Path(os.environ["WARDEN_FAKE_STATE"]).unlink(missing_ok=True)
    dec, inst = seed()
    env = dict(os.environ)
    core = subprocess.Popen([sys.executable, "-m", "uvicorn", "warden.main:app", "--port", "18096", "--log-level", "warning"], env=env)
    ui_log = open(f"{S}/ui2_server.log", "w")
    ui_ = subprocess.Popen([sys.executable, "-m", "warden.ui2.app"], env=env, stdout=ui_log, stderr=subprocess.STDOUT)
    res = {}
    try:
        import httpx
        for _ in range(60):
            try:
                if httpx.get("http://127.0.0.1:8096/health", timeout=3).status_code == 200 and httpx.get("http://127.0.0.1:18096/health", timeout=3).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(); ctx = b.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="Asia/Jakarta"); pg = ctx.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            for path, name in PAGES + [("/incidents/" + dec.incident_id, "incident"), ("/incidents/" + dec.incident_id + "?tab=timeline", "incident_timeline"), ("/incidents/" + dec.incident_id + "?tab=decisions", "incident_decisions")]:
                r = pg.goto("http://127.0.0.1:8096" + path, wait_until="load", timeout=90000); pg.wait_for_timeout(600)
                res[f"http_{name}"] = r.status == 200
                pg.screenshot(path=f"{S}/{name}.png", full_page=True)
            res["no_js_errors"] = not errors
            body = pg.goto("http://127.0.0.1:8096/", wait_until="load", timeout=90000) and pg.inner_text("body")
            res["overview_has_decision"] = "Needs your decision" in body and "Approve" in body
            res["tz_is_gmt7"] = "GMT+7" in body
            pg.click("button.btn-freeze"); pg.wait_for_timeout(2500)
            res["frozen_after_click"] = bool(db.client().collection("policies").document("runtime").get().to_dict().get("frozen"))
            pg.click("button.btn-thaw"); pg.wait_for_timeout(2500)
            res["thawed_after_click"] = not bool(db.client().collection("policies").document("runtime").get().to_dict().get("frozen"))
            pg.goto("http://127.0.0.1:8096/incidents/" + dec.incident_id, wait_until="load", timeout=90000)
            pg.click("aside.decision button.btn-approve"); pg.wait_for_timeout(3000)
            res["decision_done"] = db.decisions.get(dec.decision_id).status == DecisionStatus.DONE
            res["machine_stopped"] = registry.compute().describe(inst.ref).status == "STOPPED"
            m = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            for path, name in (("/", "overview_mobile"), ("/incidents/" + dec.incident_id, "incident_mobile")):
                m.goto("http://127.0.0.1:8096" + path, wait_until="load", timeout=90000); m.wait_for_timeout(600); m.screenshot(path=f"{S}/{name}.png", full_page=True)
                res[f"mobile_no_hscroll_{name}"] = m.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            b.close()
        print(json.dumps(res, indent=1)); print("UI2-LIVE", "OK" if all(res.values()) else "FAIL")
    except Exception:
        ui_log.flush(); print(open(f"{S}/ui2_server.log").read()[-1500:]); raise
    finally:
        ui_.terminate(); core.terminate()


if __name__ == "__main__":
    main()
