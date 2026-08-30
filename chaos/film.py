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
BROWSER_W = 1180                       # left pane: the dashboard; right pane: the terminal
CHROME_TOP = 85                        # tab strip + address bar, measured on this build
UI = os.environ.get("FILM_UI", "https://warden-ui-603873318528.us-central1.run.app")

CAPTION = """(t) => {
  let el = document.getElementById('film-caption');
  if (!el) {
    el = document.createElement('div'); el.id = 'film-caption';
    el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;background:rgba(10,12,16,.92);'
      + 'color:#fff;font:600 20px/1.5 ui-sans-serif,system-ui,sans-serif;padding:14px 22px;text-align:center;'
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
                ["xterm", "-geometry", f"96x74+{BROWSER_W + 6}+0", "-fa", "DejaVu Sans Mono", "-fs", "11",
                 "-bg", "#0e1116", "-fg", "#d0d0d0", "-b", "10", "+sb", "-e", "bash", "-lc", self.terminal_cmd],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(1.5)

        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=False, env=env, args=[
            "--window-position=0,0", f"--window-size={BROWSER_W},{H}", "--disable-infobars",
            "--force-device-scale-factor=1", "--disable-features=TranslateUI",
            "--disable-blink-features=AutomationControlled"])
        # an explicit viewport is what actually sizes the window headed: viewport=None leaves Chromium at 1280x720
        self.ctx = self.browser.new_context(viewport={"width": BROWSER_W, "height": H - CHROME_TOP}, timezone_id="Asia/Jakarta")
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
        print(f"\nraw video → {self.raw}")
        print(f"captions  → {OUT / f'{self.name}-captions.srt'}")

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
        g = self.page.evaluate("() => ({sx: window.screenX, sy: window.screenY, "
                               "top: window.outerHeight - window.innerHeight})")
        return (int(g["sx"] + box["x"] + box["width"] / 2),
                int(g["sy"] + g["top"] + box["y"] + box["height"] / 2))

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
        self.page.goto(UI + path, wait_until="load", timeout=60000)
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


def _wait(s: "Stage", desc: str, fn, timeout: int = 900, every: float = 3.0):
    """Wait for the world to change, keeping the caption up so the viewer knows what is being waited for."""
    t = time.time()
    while time.time() - t < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def scene_demo(s: Stage) -> None:
    """The take. Everything on screen is live: a real machine, real incidents, a real approval from a phone."""
    import os as _os
    _os.environ.setdefault("WARDEN_PROJECT", (ROOT / ".gcp_project").read_text().strip())
    _os.environ.setdefault("WARDEN_PROVIDER", "gce")
    from warden.store import firestore as db

    JOB = (ROOT / "docs/video/.job").read_text().strip()

    s.say("Warden — an SRE agent for long-running compute jobs", 5.0)
    s.say("A live machine is not correct training. Finished is not intact.", 5.5)
    s.say("Right now: one job training on a Spot machine in Compute Engine", 2.0)
    s.read(6.0, scrolls=1)

    s.say("Warden is watching it — heartbeats, phase markers, signed completion markers", 1.0)
    s.goto("/jobs", 1.5)
    s.read(6.0, scrolls=1)

    s.say("The job is about to hit a GPU out-of-memory error at step 600", 1.0)
    inc = _wait(s, "incident", lambda: next((i for i in db.incidents.list(limit=100)
                                             if i.job_id == JOB and i.rule == "run_fin_nonzero"), None), 900)
    if inc:
        s.say("It died. Warden opened an incident in seconds — nobody was watching", 3.5)
        s.goto("/incidents", 1.5)
        s.read(5.0)
        s.goto(f"/incidents/{inc.incident_id}", 2.0)
        s.say("Evidence first: the log line, quoted from the run's own log", 1.0)
        s.read(7.0, scrolls=2)
        s.say("Then the diagnosis — and every quote is checked against the raw log before anything happens", 8.0)
        s.read(4.0, scrolls=2)

        dec = _wait(s, "decision", lambda: next((d for d in [db.decisions.get(x) for x in
                                                 (db.incidents.get(inc.incident_id).decision_ids or [])]
                                                 if d and str(d.action) == "resume_job"), None), 600)
        if dec:
            s.say("Warden decided by itself: resume at half the batch size. No human was asked.", 8.0)
            s.goto(f"/incidents/{inc.incident_id}", 2.0)
            s.read(6.0, scrolls=2)
            s.say("And then it checks the world: did the new run actually pass the step it died at?", 8.0)
            _wait(s, "verify", lambda: (db.last_heartbeat(JOB) or None) and (db.last_heartbeat(JOB).step or 0) > 650, 600)
            s.goto(f"/incidents/{inc.incident_id}", 2.0)
            s.read(7.0, scrolls=2)

    s.say("Some actions Warden may not take alone. Those go to a phone.", 6.0)
    s.goto("/approvals", 2.0)
    s.say("A card is waiting in Discord right now — the operator approves it from there", 2.0)
    before = {d.decision_id for d in db.decisions.list(status="PENDING", limit=100)}
    approved = _wait(s, "approval",
                     lambda: next((d for d in db.decisions.list(limit=100)
                                   if d.decision_id in before and str(d.status) in ("DONE", "EXECUTING", "REJECTED")), None), 420)
    s.goto("/approvals", 2.0)
    if approved:
        s.say("Approved from the phone — and executed under the same policy as everything else", 7.0)
    else:
        s.say("Every approval carries its blast radius, its cost, and an expiry", 7.0)
    s.goto("/audit", 2.0)
    s.say("Every intent and every result is written down, whoever asked for it", 8.0)
    s.read(4.0, scrolls=2)

    s.say("Warden never deletes anything — that is denied by IAM, not by good intentions", 2.0)
    s.goto("/policies", 2.0)
    s.read(8.0, scrolls=3)

    s.say("And one button stops all of it", 1.5)
    s.goto("/", 2.0)
    if s.page.locator("button.btn-freeze").count():
        s.click("button.btn-freeze", settle=3.0)
        s.say("Frozen. Warden observes and proposes, but acts on nothing.", 6.0)
        if s.page.locator("button.btn-thaw").count():
            s.click("button.btn-thaw", settle=2.5)
    s.say("A live machine is not correct training. Finished is not intact. Warden watches the work.", 7.0)
    s.say("")


SCENES = {"tour": scene_tour, "demo": scene_demo}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", default="tour", choices=sorted(SCENES))
    ap.add_argument("--job", default="", help="job id for the demo terminal pane")
    ap.add_argument("--terminal", default="", help="command to run in the right-hand terminal pane")
    ns = ap.parse_args()
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
