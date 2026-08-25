"""Pixel parity: render overview.html with the mockup's data and diff it against the approved artboard (docs/mockup-v2/Main.dc.html).
Prints the share of differing pixels; the gate is < 1.0 %."""
import json, os, sys, tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageChops
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
env = Environment(loader=FileSystemLoader(str(ROOT / "warden/ui2/templates")), autoescape=True)
T0 = "2026-08-25T12:28:00+00:00"
ctx = {
    "page": "overview", "title": "Overview", "now_iso": T0, "frozen": False, "n_open": 2, "n_pending": 1,
    "services": [{"name": "Watcher", "status": "Healthy", "cls": "ok"}, {"name": "Steward", "status": "Healthy", "cls": "ok"}, {"name": "Watchdog", "status": "Healthy", "cls": "ok"}],
    "decisions": [{"decision_id": "d1", "incident_id": "i1", "action_label": "Stop instance", "target_short": "demo-train-2", "job_id": "toy-train", "autonomy": "L1", "radius": "This job",
                   "why": "Instance is running with no active job since 10:58.", "gemini": {"conf": "0.93", "passed": True}, "expires_iso": "2026-08-25T12:57:00+00:00", "expired": False}],
    "stats": {"open": 2, "resolved_today": 10, "running": 1, "instances": 2, "burn": "$0.034", "mtd": "$0.07", "cap": "$150", "cap_pct": 1, "ettr_pct": 50, "eff_h": 0.48, "paid_h": 0.96},
    "open_rows": [{"id": "i1", "severity": "critical", "severity_label": "Critical", "title": "Orphan instance", "sub": "demo-train-2 running with no active job", "job": "toy-train", "state": "Awaiting approval", "state_cls": "warn", "opened_iso": "2026-08-25T11:28:00+00:00"},
                  {"id": "i2", "severity": "warning", "severity_label": "Warning", "title": "Orphan instance", "sub": "demo-train-1 running with no active job", "job": "climate-demo", "state": "Escalated", "state_cls": "crit", "opened_iso": "2026-08-25T07:48:00+00:00"}],
    "jobs": [{"job_id": "toy-train", "status": "Running", "status_cls": "ok", "phase": "train", "pct": 36, "line": "Step 2,150 of 6,000 · loss 0.108", "hb_text": "Heartbeat", "hb_cls": "ok", "hb_iso": "2026-08-25T12:27:32+00:00"},
             {"job_id": "climate-demo", "status": "Complete", "status_cls": "grey", "phase": "F6", "pct": 100, "line": "Verified 14:46 · pred.csv 1,030 rows", "hb_text": "Stale", "hb_cls": "warn", "hb_iso": "2026-08-25T08:34:00+00:00"}],
    "activity": [{"ts": "2026-08-25T10:52:00+00:00", "actor": "Warden", "cls": "warden", "text": "Quarantined artifact ckpt_001742.npz on demo-train-2 · Verified", "inc": "i1"},
                 {"ts": "2026-08-25T10:44:00+00:00", "actor": "Gemini", "cls": "gemini", "text": "Diagnosed run failure on toy-train as checkpoint_corrupt · confidence 0.91 · $0.012", "inc": "i1"},
                 {"ts": "2026-08-25T10:33:00+00:00", "actor": "Operator", "cls": "operator", "text": "Approved and started demo-train-2 · Running", "inc": "i1"},
                 {"ts": "2026-08-25T07:16:00+00:00", "actor": "Warden", "cls": "warden", "text": "Started demo-train-1 after preemption · resumed at phase F3 · Verified · downtime 5 min 48 s", "inc": "i2"}],
}
html = env.get_template("overview.html").render(**ctx)
html = html.replace('href="/static/', f'href="file://{ROOT}/warden/ui2/static/').replace('src="/static/', f'src="file://{ROOT}/warden/ui2/static/')
out = Path(tempfile.mkdtemp()); (out / "render.html").write_text(html)
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx_ = b.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="Asia/Jakarta")
    ctx_.add_init_script("Date.now = () => 1787660880000")  # 2026-08-25T12:28:00Z — the mockup's clock
    pg = ctx_.new_page(); pg.goto(f"file://{out}/render.html", wait_until="load"); pg.wait_for_timeout(1500); pg.screenshot(path=str(out / "render.png"), full_page=False)
    pg2 = ctx_.new_page(); pg2.goto(f"file://{ROOT}/docs/mockup-v2/Main.dc.html", wait_until="load"); pg2.wait_for_timeout(1500); pg2.screenshot(path=str(out / "mockup.png"), full_page=False)
    b.close()
a = np.asarray(Image.open(out / "render.png").convert("RGB")).astype(int); m = np.asarray(Image.open(out / "mockup.png").convert("RGB")).astype(int)
h = min(a.shape[0], m.shape[0]); w = min(a.shape[1], m.shape[1]); a, m = a[:h, :w], m[:h, :w]
diff = (np.abs(a - m).max(axis=2) > 24)
pct = 100 * diff.mean()
Image.fromarray((diff * 255).astype("uint8")).save(out / "diff.png")
Image.open(out / "render.png").save(ROOT / "docs/screenshots/ui2/pixel_render.png"); Image.open(out / "mockup.png").save(ROOT / "docs/screenshots/ui2/pixel_mockup.png"); Image.fromarray((diff * 255).astype("uint8")).save(ROOT / "docs/screenshots/ui2/pixel_diff.png")
print(json.dumps({"differing_pixels_pct": round(pct, 3), "size": [w, h], "gate_lt_1pct": bool(pct < 1.0)}))
