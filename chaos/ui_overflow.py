"""Find what actually overflows the phone viewport, element by element (K1 follow-up).

The tour reported horizontal scroll on /budget, /policies, /audit and /ask at 390 px. Guessing at CSS is how you fix the
symptom and keep the bug, so this walks the DOM and names every element wider than the viewport, with its tag, classes and
measured width — then the fix can be aimed at the real offender.

    python -m chaos.ui_overflow            # prints a table per page
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("TOUR_OUT", ROOT / "docs/video/tour"))
CORE_PORT, UI_PORT = "18099", "8099"
os.environ.update({
    "FIRESTORE_EMULATOR_HOST": "127.0.0.1:8081", "WARDEN_PROJECT": "warden-local", "WARDEN_FIRESTORE_DB": "warden-tour",
    "WARDEN_PROVIDER": "fake", "WARDEN_DEV": "1", "WARDEN_FAKE_STATE": str(OUT / "fleet.json"),
    "WARDEN_CORE_URL": f"http://127.0.0.1:{CORE_PORT}", "PORT": UI_PORT,
})
UI = f"http://127.0.0.1:{UI_PORT}"

PAGES = ["/", "/incidents", "/approvals", "/jobs", "/jobs/vision-7b", "/jobs/launch", "/fleet", "/budget",
         "/policies", "/audit", "/system", "/ask"]


def incident_paths() -> list[str]:
    """The incident page has four tabs and only one of them was ever measured."""
    from warden.store import firestore as db
    inc = next((i for i in db.incidents.list(limit=400) if i.diagnosis), db.incidents.list(limit=1)[0])
    return [f"/incidents/{inc.incident_id}{t}" for t in ("", "?tab=timeline", "?tab=decisions", "?tab=evidence")]

FIND = """(vw) => {
  const out = [];
  document.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > vw + 1 || r.right > vw + 1) {
      const p = el.parentElement;
      out.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className && el.className.toString().slice(0, 60)) || '',
        w: Math.round(r.width), right: Math.round(r.right),
        scrollW: el.scrollWidth, clientW: el.clientWidth,
        overflowX: getComputedStyle(el).overflowX,
        parent: p ? p.tagName.toLowerCase() + '.' + ((p.className && p.className.toString().slice(0, 40)) || '') : '',
        text: (el.textContent || '').trim().slice(0, 50).replace(/\\s+/g, ' ')
      });
    }
  });
  return out;
}"""


def main() -> int:
    env = dict(os.environ)
    logs = open(OUT / "overflow_servers.log", "w")
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
    findings = {}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 390, "height": 844}, timezone_id="Asia/Jakarta")
            pg = ctx.new_page()
            for path in PAGES + incident_paths():
                pg.goto(UI + path, wait_until="load", timeout=60000)
                pg.wait_for_timeout(500)
                doc = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                bad = pg.evaluate(FIND, 390)
                findings[path] = {"doc_scroll_width": doc[0], "inner_width": doc[1], "elements": bad}
                flag = "OVERFLOW" if doc[0] > doc[1] + 1 else "ok      "
                print(f"{flag} {path:12s} scrollWidth={doc[0]:5d} inner={doc[1]}")
                for e in bad[:6]:
                    print(f"          <{e['tag']} class=\"{e['cls']}\"> w={e['w']} right={e['right']} "
                          f"scrollW={e['scrollW']} overflowX={e['overflowX']} parent={e['parent']}")
                    if e["text"]:
                        print(f"            text: {e['text']}")
            b.close()
    finally:
        core.terminate(); ui.terminate()
        (OUT / "overflow_report.json").write_text(json.dumps(findings, indent=1))
        print("\nreport →", OUT / "overflow_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
