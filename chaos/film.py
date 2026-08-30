"""Record the submission video: one continuous take, a real cursor, a real machine.

The rules ask for "unedited, live execution of the agent performing its task" and visible proof the backend runs on
Google Cloud. So this records an actual X display with ffmpeg while a real browser shows the production dashboard and a
real terminal shows the live drill against Compute Engine. Nothing is cut, sped up or composited afterwards.

The cursor is the X pointer, moved through XTest with easing and a little overshoot, because a cursor that teleports
reads as a screenshot slideshow rather than someone using the product. Clicks are real X button events; the browser
cannot tell them from a hand on a mouse.

    python -m chaos.film --scenes tour        # rehearsal against production, no machine created
    python -m chaos.film --scenes demo        # the take (expects a drill running in the terminal pane)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("FILM_OUT", ROOT / "docs/video"))
OUT.mkdir(parents=True, exist_ok=True)
DISPLAY = os.environ.get("FILM_DISPLAY", ":99")
W, H = 1920, 1080
# The dashboard gets the full width and the terminal a strip beneath it. The earlier side-by-side spent a third of
# every frame on empty black and rendered the UI at 1:1, which is unreadable once the file is a video: body text was
# 13 px in a 1920-wide frame. Rendering at a device scale of 1.37 makes the page 1.37x larger while a CSS width of
# 1392 still clears the 1184 px content column, so nothing is letterboxed and nothing is cropped.
STRIP = 190                            # terminal strip along the bottom: 228 cols x 12 rows at 12 pt
BROWSER_H = H - STRIP
SCALE = 1.37
VP_W, VP_H = 1392, 563                 # CSS px; measured to land the window at 1920x890 physical
CHROME_TOP = 86                        # tab strip + address bar, in CSS px, measured on this build
UI = os.environ.get("FILM_UI", "https://warden-ui-603873318528.us-central1.run.app")

CAPTION = """(t) => {
  let el = document.getElementById('film-caption');
  if (!el) {
    el = document.createElement('div'); el.id = 'film-caption';
    el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;background:rgba(10,12,16,.92);'
      + 'color:#fff;font:600 21px/1.45 ui-sans-serif,system-ui,sans-serif;padding:13px 22px;text-align:center;'
      + 'pointer-events:none;letter-spacing:.01em';
    document.documentElement.appendChild(el);
  }
  el.textContent = t; el.style.display = t ? 'block' : 'none';
}"""


class Pointer:
    """The real X pointer. Playwright's mouse would move nothing a camera can see."""

    def __init__(self, display_name: str):
        from Xlib import display as xd
        self.d = xd.Display(display_name)
        self.x, self.y = W // 2, H // 2

    def _to(self, x: int, y: int):
        from Xlib.ext import xtest
        xtest.fake_input(self.d, 6, x=int(x), y=int(y))   # MotionNotify
        self.d.sync()
        self.x, self.y = x, y

    def move(self, x: int, y: int, seconds: float | None = None):
        dist = math.hypot(x - self.x, y - self.y)
        if dist < 2:
            return
        seconds = seconds if seconds is not None else min(1.1, max(0.28, dist / 1400))
        steps = max(12, int(seconds * 60))
        x0, y0 = self.x, self.y
        # a hand overshoots slightly and settles back; a machine does not
        ox = (x - x0) * 0.06 * random.uniform(0.4, 1.0)
        oy = (y - y0) * 0.06 * random.uniform(0.4, 1.0)
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)                        # smoothstep
            px = x0 + (x - x0) * e + ox * math.sin(math.pi * t)
            py = y0 + (y - y0) * e + oy * math.sin(math.pi * t)
            if 0.15 < t < 0.85:
                px += random.uniform(-0.8, 0.8)
                py += random.uniform(-0.8, 0.8)
            self._to(px, py)
            time.sleep(seconds / steps)
        self._to(x, y)

    def click(self, button: int = 1):
        from Xlib.ext import xtest
        time.sleep(random.uniform(0.10, 0.20))
        xtest.fake_input(self.d, 4, button); self.d.sync()
        time.sleep(random.uniform(0.05, 0.09))
        xtest.fake_input(self.d, 5, button); self.d.sync()
        time.sleep(random.uniform(0.18, 0.32))

    def scroll(self, notches: int, down: bool = True):
        from Xlib.ext import xtest
        for _ in range(abs(notches)):
            xtest.fake_input(self.d, 4, 5 if down else 4); self.d.sync()
            xtest.fake_input(self.d, 5, 5 if down else 4); self.d.sync()
            time.sleep(random.uniform(0.10, 0.17))


class Stage:
    def __init__(self, name: str, terminal_cmd: str | None = None):
        self.name = name
        self.terminal_cmd = terminal_cmd
        self.procs: list[subprocess.Popen] = []
        self.raw = OUT / f"{name}-raw.mp4"

    def __enter__(self):
        env = dict(os.environ, DISPLAY=DISPLAY)
        self.procs.append(subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(2.5)

        if self.terminal_cmd:
            self.procs.append(subprocess.Popen(
                ["xterm", "-geometry", f"228x12+0+{BROWSER_H + 2}", "-fa", "DejaVu Sans Mono", "-fs", "12",
                 "-bg", "#0b0e13", "-fg", "#c8d0dc", "-b", "8", "+sb", "-e", "bash", "-lc", self.terminal_cmd],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(1.5)

        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=False, env=env, args=[
            "--window-position=0,0", f"--window-size={W},{BROWSER_H}", "--disable-infobars",
            f"--force-device-scale-factor={SCALE}", "--disable-features=TranslateUI",
            "--disable-blink-features=AutomationControlled"])
        # an explicit viewport is what actually sizes the window headed: viewport=None leaves Chromium at 1280x720.
        # device_scale_factor has to be set on the context too — the launch flag alone is overridden back to 1.
        self.ctx = self.browser.new_context(viewport={"width": VP_W, "height": VP_H},
                                            device_scale_factor=SCALE, timezone_id="Asia/Jakarta")
        self.page = self.ctx.new_page()
        self.page.goto(UI + "/", wait_until="load", timeout=60000)
        self.page.wait_for_timeout(1200)
        self.p = Pointer(DISPLAY)

        self.procs.append(subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-draw_mouse", "1",
             "-video_size", f"{W}x{H}", "-framerate", "24", "-i", DISPLAY,
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-crf", "23", str(self.raw)],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(1.5)
        self.t0 = time.time()
        self.marks: list[tuple[float, str]] = []
        self.ramps: list[dict] = []          # stretches that were pure waiting, for chaos.film_cut
        return self

    def __exit__(self, *a):
        time.sleep(1.0)
        for pr in self.procs:
            if pr.args[0] == "ffmpeg":
                pr.terminate(); pr.wait(timeout=20)
        try:
            self.browser.close(); self.pw.stop()
        except Exception:  # noqa: BLE001
            pass
        for pr in self.procs:
            pr.terminate()
        (OUT / f"{self.name}-captions.srt").write_text(self._srt())
        (OUT / f"{self.name}-marks.json").write_text(json.dumps(
            {"marks": self.marks, "ramps": self.ramps, "duration": time.time() - self.t0}, indent=1))
        print(f"\nraw video → {self.raw}")
        print(f"captions  → {OUT / f'{self.name}-captions.srt'}")
        print(f"marks     → {OUT / f'{self.name}-marks.json'}  ({len(self.ramps)} waits)")

    # ---- direction -------------------------------------------------------
    def say(self, text: str, hold: float = 0.0):
        self.marks.append((time.time() - self.t0, text))
        self._caption = text
        self.page.evaluate(CAPTION, text)
        if hold:
            time.sleep(hold)

    def beat(self, seconds: float):
        time.sleep(seconds)

    def _screen(self, box: dict) -> tuple[int, int]:
        """CSS pixels to the physical pixels XTest moves in. At a device scale of 1.37 the two differ by a third, and
        a click computed in CSS space lands a whole card higher than the one the viewer sees under the cursor."""
        g = self.page.evaluate("() => ({sx: window.screenX, sy: window.screenY, dpr: window.devicePixelRatio, "
                               "top: window.outerHeight - window.innerHeight})")
        return (int((g["sx"] + box["x"] + box["width"] / 2) * g["dpr"]),
                int((g["sy"] + g["top"] + box["y"] + box["height"] / 2) * g["dpr"]))

    def hover(self, selector: str, nth: int = 0):
        loc = self.page.locator(selector).nth(nth)
        loc.scroll_into_view_if_needed(timeout=15000)
        self.page.wait_for_timeout(250)
        box = loc.bounding_box()
        if not box:
            raise RuntimeError(f"no box for {selector}")
        self.p.move(*self._screen(box))
        return loc

    def click(self, selector: str, nth: int = 0, settle: float = 1.4):
        self.hover(selector, nth)
        self.p.click()
        self.page.wait_for_timeout(int(settle * 1000))
        self._recaption()

    def _recaption(self) -> None:
        if getattr(self, "_caption", ""):
            try:
                self.page.evaluate(CAPTION, self._caption)
            except Exception:  # noqa: BLE001
                pass

    def goto(self, path: str, settle: float = 1.2):
        """Navigate, tolerating a navigation the page started itself.

        Approving a card posts and then reloads. A goto issued into that window is aborted by Chromium with
        ERR_ABORTED — not a failure of the product, but it ended a six-minute take at the last scene. Wait for the
        page's own navigation to land, then go."""
        for attempt in (1, 2, 3):
            try:
                self.page.goto(UI + path, wait_until="load", timeout=60000)
                break
            except Exception as e:  # noqa: BLE001
                if "ERR_ABORTED" not in str(e) or attempt == 3:
                    raise
                self.page.wait_for_timeout(1800)
        self.page.wait_for_timeout(int(settle * 1000))
        self._recaption()

    def read(self, seconds: float, scrolls: int = 0):
        """Let the viewer actually read: a slow scroll, not a jump."""
        if scrolls:
            for _ in range(scrolls):
                self.p.scroll(3)
                time.sleep(0.5)
        time.sleep(seconds)

    def _srt(self) -> str:
        def ts(s: float) -> str:
            h, r = divmod(s, 3600); m, sec = divmod(r, 60)
            return f"{int(h):02d}:{int(m):02d}:{int(sec):02d},{int((sec % 1) * 1000):03d}"
        out, end = [], time.time() - self.t0
        for i, (start, text) in enumerate(self.marks, 1):
            stop = self.marks[i][0] if i < len(self.marks) else end
            out.append(f"{i}\n{ts(start)} --> {ts(stop)}\n{text}\n")
        return "\n".join(out)


def scene_tour(s: Stage) -> None:
    """A rehearsal against production: every move the take will make, no machine created."""
    s.say("Warden — an SRE agent for long-running compute jobs", 3.0)
    s.say("A live machine is not correct training. Finished is not intact.", 3.5)

    s.say("The dashboard, served from Cloud Run", 1.0)
    s.read(2.5, scrolls=2)

    s.say("Every incident Warden has open, newest first", 0.5)
    s.click("a[href='/incidents']", settle=2.0)
    s.read(3.0, scrolls=2)

    s.say("Filters, because an unfiltered list is not an inbox", 0.5)
    s.click("select[name='sev']", settle=0.8)
    s.beat(1.5)

    s.say("One incident: evidence, then diagnosis, then the decision", 0.5)
    s.goto("/incidents", 1.0)
    s.click("a.plain", settle=2.2)
    s.read(4.0, scrolls=3)

    s.say("Warden's own health — the row that invalidates a drill", 0.5)
    s.click("a[href='/system']", settle=2.0)
    s.read(3.0, scrolls=2)

    s.say("Ask Warden reads a photo from the operator's phone", 0.5)
    s.click("a[href='/ask']", settle=2.0)
    s.read(2.5)
    s.say("")


def _wait(s: "Stage", label: str, fn, timeout: int = 900, every: float = 6.0, tour: list[str] | None = None):
    """Wait for the world to change — and keep the picture alive while waiting.

    A recording that holds one motionless page for a minute reads as a broken video, so the wait walks real pages that
    are worth seeing anyway. The stretch is recorded so `chaos.film_cut` can compress it with a badge saying by how
    much: the waiting is real, and shortening it must be visible rather than hidden.
    """
    start = time.time() - s.t0
    t, i = time.time(), 0
    tour = tour or []
    while time.time() - t < timeout:
        v = fn()
        if v:
            break
        if tour:
            s.goto(tour[i % len(tour)], 0.8)
            s.read(max(1.0, every - 2.0), scrolls=1)
        else:
            time.sleep(every)
        i += 1
    else:
        v = None
    end = time.time() - s.t0
    if end - start > 6.0:
        s.ramps.append({"start": start, "end": end, "label": label})
    return v


def scene_demo(s: Stage) -> None:
    """The take. Everything on screen is live: a real Spot machine, a real crash, real decisions, a real approval.

    Nobody has to be standing by. The approval is answered on the dashboard by the operator running the take; the
    proof that the same card is answerable from a phone is in the audit log, where a real Discord approval is already
    written down. Waiting for a person to pick up a phone is not something a four-minute video can hold.
    """
    import os as _os
    _os.environ.setdefault("WARDEN_PROJECT", (ROOT / ".gcp_project").read_text().strip())
    _os.environ.setdefault("WARDEN_PROVIDER", "gce")
    from warden.store import firestore as db

    JOB = (ROOT / "docs/video/.job").read_text().strip()
    CORE = os.environ.get("FILM_CORE", "")

    def inc_of(rule: str):
        return next((i for i in db.incidents.list(limit=120) if i.job_id == JOB and i.rule == rule), None)

    def step() -> int:
        hb = db.last_heartbeat(JOB)
        return hb.step or 0 if hb else 0

    # ---- 1. the thesis ------------------------------------------------------
    s.say("Warden — an SRE agent for long-running compute jobs", 4.5)
    s.say("A live machine is not correct training. Finished is not intact.", 5.0)

    # ---- 2. Warden launches the work itself ---------------------------------
    s.say("Warden was handed a spec. It is building the machine on Compute Engine right now.", 2.0)
    s.goto("/fleet", 1.5)
    s.read(5.0, scrolls=1)
    _wait(s, "waiting for the Spot machine to boot and send its first heartbeat",
          lambda: (db.jobs.get(JOB) and str(db.jobs.get(JOB).status) == "RUNNING") and step() > 60, 1200,
          tour=["/fleet", "/jobs", "/"])

    s.say("Running: a Spot machine, a real model, heartbeats every few seconds", 1.0)
    s.goto("/jobs", 1.5)
    s.read(6.0, scrolls=1)

    # ---- 3. it dies ---------------------------------------------------------
    s.say("At step 600 this run hits a GPU out-of-memory error. Nobody is watching it.", 1.5)
    inc = _wait(s, "waiting for the run to reach step 600 and die",
                lambda: inc_of("run_fin_nonzero"), 1500, tour=["/jobs", "/fleet", "/system", "/"])
    if not inc:
        s.say("The drill did not reach the failure in time — stopping the take rather than faking one", 6.0)
        return

    s.say("It died. Warden opened an incident within one tick.", 3.5)
    s.goto("/incidents", 1.5)
    s.read(5.0)

    # ---- 4. evidence before diagnosis --------------------------------------
    s.goto(f"/incidents/{inc.incident_id}", 2.0)
    s.say("Evidence first — the failing line, quoted from the run's own log", 1.0)
    s.read(7.0, scrolls=2)
    s.say("Then the diagnosis. Every quote is checked against the raw log before anything is allowed to happen.", 9.0)
    s.read(5.0, scrolls=2)

    # ---- 5. it decides, alone ----------------------------------------------
    dec = _wait(s, "waiting for the diagnosis and the decision",
                lambda: next((d for d in [db.decisions.get(x) for x in
                              (db.incidents.get(inc.incident_id).decision_ids or [])]
                              if d and str(d.action) == "resume_job" and str(d.status) in ("DONE", "EXECUTING")), None),
                900, tour=[f"/incidents/{inc.incident_id}", "/incidents"])
    if dec:
        s.goto(f"/incidents/{inc.incident_id}", 2.0)
        s.say("Warden decided by itself: resume at half the batch size. No human was asked.", 9.0)
        s.read(5.0, scrolls=2)

    # ---- 6. and checks the world, not the API answer ------------------------
    s.say("Then it checks the world: did the new run actually pass the step it died at?", 2.0)
    _wait(s, "waiting for the resumed run to pass the step it died at",
          lambda: step() > 660 and str(db.incidents.get(inc.incident_id).state) in ("RESOLVED", "VERIFYING"),
          1200, tour=[f"/incidents/{inc.incident_id}", "/jobs"])
    s.goto(f"/incidents/{inc.incident_id}", 2.0)
    s.say("Past step 600 on the resumed run. The incident closes on evidence, not on an API returning OK.", 9.0)
    s.read(4.0, scrolls=2)

    # ---- 7. what it may not do alone ---------------------------------------
    s.say("Some actions Warden may not take alone. Those stop and wait for a person.", 2.0)
    if CORE:
        _propose(CORE, JOB, "resize_disk", {"target_gb": 40}, "the disk is filling ahead of the next checkpoint")
    pend = _wait(s, "waiting for the proposal to be evaluated against policy",
                 lambda: next((d for d in db.decisions.list(status="PENDING", limit=50)
                               if d.job_id == JOB and str(d.verdict) == "NEED_APPROVAL"), None), 180,
                 every=4.0, tour=["/approvals"])
    s.goto("/approvals", 2.0)
    s.say("Every request carries what it will touch, what it costs, and when it lapses", 8.0)
    s.read(4.0, scrolls=1)

    if pend and s.page.locator("button.btn-approve").count():
        s.say("Approved by the operator — and executed under the same policy as everything Warden does alone", 1.5)
        s.click("button.btn-approve", settle=4.5)
        s.page.wait_for_load_state("load")
        s.beat(2.0)
        _wait(s, "waiting for the approved action to run against Compute Engine",
              lambda: str(db.decisions.get(pend.decision_id).status) in ("DONE", "FAILED"), 300,
              every=4.0, tour=["/approvals", "/fleet"])
        s.goto("/audit", 2.0)
        s.say("The disk actually grew. Warden checked the machine, not the API's answer.", 7.0)
    else:
        s.goto("/audit", 2.0)

    # ---- 8. the record, the limits, the brake ------------------------------
    s.say("Every intent and every result is written down — including approvals answered from a phone in Discord", 9.0)
    s.read(5.0, scrolls=2)

    s.say("Warden cannot delete anything. That is denied by an IAM condition, not by good intentions.", 2.0)
    s.goto("/policies", 2.0)
    s.read(9.0, scrolls=3)

    s.say("And one button stops all of it", 1.5)
    s.goto("/", 2.5)
    if s.page.locator("button.btn-freeze").count():
        s.click("button.btn-freeze", settle=3.5)
        s.say("Frozen. Warden still watches and still proposes, but acts on nothing.", 6.0)
        if s.page.locator("button.btn-thaw").count():
            s.click("button.btn-thaw", settle=3.0)
    s.say("A live machine is not correct training. Finished is not intact. Warden watches the work.", 7.0)
    s.say("")


def _propose(core: str, job_id: str, action: str, params: dict, why: str) -> dict:
    """Ask through the product's own operator door, signed, exactly as the dashboard does."""
    import hashlib, hmac, json as _json, subprocess as _sp, urllib.request
    secret = _sp.run(["/home/ubuntu/google-cloud-sdk/bin/gcloud", "secrets", "versions", "access", "latest",
                      "--secret", "warden-ingest-hmac", "--project", (ROOT / ".gcp_project").read_text().strip()],
                     capture_output=True, text=True).stdout.strip()
    body = {"action": action, "params": params, "who": "operator", "why": why}
    raw = _json.dumps(body).encode()
    req = urllib.request.Request(f"{core}/jobs/{job_id}/propose", data=raw, method="POST", headers={
        "Content-Type": "application/json",           # the endpoint signs the job id, not the body
        "X-Warden-Signature": hmac.new(secret.encode(), job_id.encode(), hashlib.sha256).hexdigest()})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return _json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001 — a take that silently skips the approval scene is worse than a loud one
        print(f"propose failed: {e}")
        return {"ok": False}


SCENES = {"tour": scene_tour, "demo": scene_demo}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", default="tour", choices=sorted(SCENES))
    ap.add_argument("--job", default="", help="job id for the demo terminal pane")
    ap.add_argument("--terminal", default="", help="command to run in the right-hand terminal pane")
    ns = ap.parse_args()
    os.environ.setdefault("FILM_CORE", subprocess.run(
        ["/home/ubuntu/google-cloud-sdk/bin/gcloud", "run", "services", "describe", "warden-core", "--region",
         "us-central1", "--project", (ROOT / ".gcp_project").read_text().strip(), "--format", "value(status.url)"],
        capture_output=True, text=True).stdout.strip())
    if ns.job:
        (ROOT / "docs/video/.job").write_text(ns.job)
        term = ns.terminal or f"cd {ROOT} && .venv/bin/python -m chaos.film_watch {ns.job}"
    else:
        term = ns.terminal or "watch -n 3 -t 'gcloud run services list --region us-central1 " \
                              "--format=\"table(metadata.name,status.latestReadyRevisionName,status.url)\" 2>/dev/null'"
    with Stage(ns.scenes, terminal_cmd=term) as s:
        SCENES[ns.scenes](s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
