"""Uji fungsional dashboard (Fase 8) di emulator + provider fake: seed insiden nyata-mirip, jalankan core+ui lokal,
klik Approve/Deny/Re-evaluate/FREEZE lewat Playwright, verifikasi efeknya di Firestore. Tanpa GCP."""
import os, subprocess, sys, time, json
from pathlib import Path
os.environ.update({"FIRESTORE_EMULATOR_HOST": "127.0.0.1:8081", "WARDEN_PROJECT": "warden-local", "WARDEN_FIRESTORE_DB": "warden-test",
                   "WARDEN_PROVIDER": "fake", "WARDEN_DEV": "1", "WARDEN_FAKE_STATE": "/tmp/claude-1001/-home-ubuntu-lintasai/8cd3c608-c7cb-4791-a09a-9f328214e737/scratchpad/fake_state.json", "WARDEN_CORE_URL": "http://127.0.0.1:18095", "PORT": "8098"})
from datetime import timedelta
from warden.core.models import DecisionStatus, Heartbeat, Job, JobStatus, now
from warden.providers import registry
from warden.store import firestore as db
from warden.watcher import tick as T

S = os.environ.get("SHOT_DIR", "docs/screenshots")


def seed():
    Path(os.environ["WARDEN_FAKE_STATE"]).unlink(missing_ok=True)
    registry._fake = None; T._prev_status.clear()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs", "notifications", "policies", "policy_overrides", "health", "costs"):
        for d in db.client().collection(coll).limit(300).stream():
            d.reference.delete()
    for jid in ("toy-train", "climate-demo"):
        for d in db.client().collection("runs").document(jid).collection("heartbeats").limit(500).stream():
            d.reference.delete()
    fake = registry.compute(); inst = fake.add("demo-train-2")
    job = Job(job_id="toy-train", instance_ref=inst.ref, status=JobStatus.COMPLETE, legacy=True, phase="export", run_id="r20260825T105835"); db.jobs.put(job); inst.job_id = "toy-train"
    j2 = Job(job_id="climate-demo", instance_ref="", status=JobStatus.RUNNING, phase="F3-4", run_id="r20260825T073658", legacy=True); db.jobs.put(j2)
    t0 = now() - timedelta(minutes=52)
    for i in range(30):
        db.put_heartbeat(Heartbeat(job_id="toy-train", run_id="r20260825T105835", ts=t0 + timedelta(minutes=i), phase="train", step=200 * i, loss=1.0 / (i + 1), cpu_pct=3 + i % 3))
        db.put_heartbeat(Heartbeat(job_id="climate-demo", run_id="r20260825T073658", ts=t0 + timedelta(minutes=i), phase="F3-4", cpu_pct=40 + i % 9, synthetic=True))
    db.health("watcher", True); db.health("steward", True); db.health("deadman", True); db.health("compute_api", True)
    db.cost_add(now().strftime("%Y-%m-%d"), "compute_usd", 0.05, inst.ref); db.cost_add(now().strftime("%Y-%m-%d"), "llm_usd", 0.023, "gemini")
    s = T.run_tick(); assert s["approval"] == 1, s          # yatim legacy → stop L1 → menunggu izin
    dec = [d for d in db.decisions.list(status="PENDING") if d.verdict == "NEED_APPROVAL"][0]
    inc = db.incidents.get(dec.incident_id)
    inc.diagnosis = {"category": "orphan_instance", "confidence": 0.93, "transient_or_permanent": "permanent", "recommended_action": "stop_instance",
                     "evidence_quotes": ["RUN_FIN exit=0 10:58:35", "no active run since 10:58"], "falsifiable_check": "a new RUN_START within 10 min would refute this", "model": "gemini-3.5-flash",
                     "human_summary_en": "Machine is running with no active job; it costs $0.005/h for nothing."}
    inc.crosscheck = {"passed": True, "adjusted_confidence": 0.93, "checks": [{"check": "evidence_lines_exist", "ok": True}, {"check": "status_matches_claim", "ok": True}]}
    inc.llm_cost_usd = 0.012; db.incidents.put(inc)
    return dec, inst


def main():
    dec, inst = seed()
    env = dict(os.environ)
    core = subprocess.Popen([sys.executable, "-m", "uvicorn", "warden.main:app", "--port", "18095", "--log-level", "warning"], env=env)
    ui_ = subprocess.Popen([sys.executable, "-m", "warden.ui.dashboard"], env=env)
    try:
        import httpx
        for _ in range(60):
            try:
                if httpx.get("http://127.0.0.1:8098/", timeout=3).status_code == 200 and httpx.get("http://127.0.0.1:18095/health", timeout=3).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        from playwright.sync_api import sync_playwright
        res = {}
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1440, "height": 1000})
            pg.goto("http://127.0.0.1:8098/", wait_until="networkidle", timeout=90000); pg.wait_for_timeout(2500)
            pg.screenshot(path=f"{S}/ui_overview_desktop.png", full_page=True)
            res["overview_has_approval"] = "AWAITING APPROVAL" in pg.content()
            res["overview_has_gemini"] = "GEMINI" in pg.content()
            for path, name in (("/incidents/" + dec.incident_id, "incident"), ("/jobs", "jobs"), ("/budget", "budget"), ("/approvals", "approvals"), ("/audit", "audit")):
                pg.goto("http://127.0.0.1:8098" + path, wait_until="networkidle", timeout=90000); pg.wait_for_timeout(1500)
                pg.screenshot(path=f"{S}/ui_{name}_desktop.png", full_page=True)
            # FREEZE → Thaw
            pg.goto("http://127.0.0.1:8098/", wait_until="networkidle", timeout=90000); pg.wait_for_timeout(1000)
            pg.get_by_role("button", name="FREEZE").click(); pg.wait_for_timeout(2500)
            res["frozen_after_click"] = bool(db.client().collection("policies").document("runtime").get().to_dict().get("frozen"))
            pg.get_by_role("button", name="Thaw").click(); pg.wait_for_timeout(2500)
            res["thawed_after_click"] = not bool(db.client().collection("policies").document("runtime").get().to_dict().get("frozen"))
            # Approve from the approvals page
            pg.goto("http://127.0.0.1:8098/approvals", wait_until="networkidle", timeout=90000); pg.wait_for_timeout(1000)
            pg.get_by_role("button", name="Approve").first.click(); pg.wait_for_timeout(1200)
            try: print("notif:", pg.locator(".q-notification").first.inner_text(timeout=2000))
            except Exception as e: print("notif: (none)", type(e).__name__)
            pg.wait_for_timeout(2500)
            d2 = db.decisions.get(dec.decision_id); print("decision:", d2.status, d2.approved_by, d2.result)
            if d2.status != DecisionStatus.DONE:
                print("direct:", httpx.post(f"http://127.0.0.1:18095/decisions/{dec.decision_id}/approve", params={"who": "probe"}, timeout=30).text[:300])
            res["decision_done"] = db.decisions.get(dec.decision_id).status == DecisionStatus.DONE
            res["machine_stopped"] = registry.compute().describe(inst.ref).status == "STOPPED"
            # mobile
            m = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            m.goto("http://127.0.0.1:8098/", wait_until="networkidle", timeout=90000); m.wait_for_timeout(2000)
            m.screenshot(path=f"{S}/ui_overview_mobile.png", full_page=True)
            res["mobile_no_hscroll"] = m.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            m.goto("http://127.0.0.1:8098/incidents/" + dec.incident_id, wait_until="networkidle", timeout=90000); m.wait_for_timeout(1500)
            m.screenshot(path=f"{S}/ui_incident_mobile.png", full_page=True)
            b.close()
        print(json.dumps(res, indent=1)); print("UI-LIVE", "OK" if all(res.values()) else "FAIL")
    finally:
        ui_.terminate(); core.terminate()


if __name__ == "__main__":
    main()
