"""Pixel-level lint of every dashboard page, desktop and phone.

Screenshots catch what you happen to look at; this catches what you do not. It walks every page at both viewports and
reports, per element: text clipped by its own box, anything crossing the viewport edge, type below the legible floor,
touch targets too small to hit on a phone, text whose contrast against its actual background fails WCAG AA, interactive
elements that overlap each other, and icons with no accessible name.

Every finding names the element, its classes and the measured numbers, so the fix can be aimed rather than guessed.

    python -m chaos.ui_lint            # → docs/video/tour/lint_report.json
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
CORE_PORT, UI_PORT = "18101", "8101"
os.environ.update({
    "FIRESTORE_EMULATOR_HOST": "127.0.0.1:8081", "WARDEN_PROJECT": "warden-local", "WARDEN_FIRESTORE_DB": "warden-tour",
    "WARDEN_PROVIDER": "fake", "WARDEN_DEV": "1", "WARDEN_FAKE_STATE": str(OUT / "fleet.json"),
    "WARDEN_CORE_URL": f"http://127.0.0.1:{CORE_PORT}", "PORT": UI_PORT,
})
UI = f"http://127.0.0.1:{UI_PORT}"

from warden.store import firestore as db  # noqa: E402

LINT = r"""(vw) => {
  const out = [];
  const A = 'a,button,input,select,textarea,[data-act],[role=button]';
  const desc = (el) => el.tagName.toLowerCase() + (el.className && el.className.toString ?
      '.' + el.className.toString().trim().split(/\s+/).slice(0, 3).join('.') : '');
  const txt = (el) => (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 48);

  // sRGB relative luminance and WCAG contrast
  const lum = (c) => { const f = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]; };
  const parse = (s) => { const m = (s || '').match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x)); return { rgb: [p[0], p[1], p[2]], a: p.length > 3 ? p[3] : 1 }; };
  const bgOf = (el) => { let e = el;
    while (e) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0.5) return c.rgb; e = e.parentElement; }
    return [255, 255, 255]; };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05); };

  const seenBoxes = [];
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || r.width === 0 || r.height === 0) return;
    const own = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());

    // 1. anything crossing the viewport edge
    if (r.right > vw + 1 || r.left < -1) out.push({ kind: 'viewport', el: desc(el), right: Math.round(r.right), left: Math.round(r.left), text: txt(el) });

    // 2. text clipped by its own box (only where the element cannot scroll)
    if (own && el.scrollWidth > el.clientWidth + 1 && ['hidden', 'clip'].includes(cs.overflowX) && !el.getAttribute('title'))
      out.push({ kind: 'clipped', el: desc(el), scrollW: el.scrollWidth, clientW: el.clientWidth, text: txt(el) });
    if (own && el.scrollHeight > el.clientHeight + 1 && ['hidden', 'clip'].includes(cs.overflowY))
      out.push({ kind: 'clipped-y', el: desc(el), scrollH: el.scrollHeight, clientH: el.clientHeight, text: txt(el) });

    // 3. type below the legible floor
    const fs = parseFloat(cs.fontSize);
    if (own && fs && fs < 11) out.push({ kind: 'tiny-type', el: desc(el), px: Math.round(fs * 10) / 10, text: txt(el) });

    // 4. contrast of real text against its real background
    if (own && txt(el)) {
      const fg = parse(cs.color);
      if (fg) {
        const c = ratio(fg.rgb, bgOf(el));
        const large = fs >= 24 || (fs >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
        if (c < (large ? 3 : 4.5)) out.push({ kind: 'contrast', el: desc(el), ratio: Math.round(c * 100) / 100, px: fs, text: txt(el) });
      }
    }

    // 5. touch targets and overlapping controls
    if (el.matches(A)) {
      // WCAG 2.5.8 exempts a link inline in a sentence, and a row-stretching ::after makes the row the hit area.
      const stretched = getComputedStyle(el, '::after').position === 'absolute';
      const parentText = el.parentElement ? (el.parentElement.textContent || '').trim() : '';
      const inlineInSentence = el.tagName === 'A' && parentText.length > txt(el).length + 2;
      if (vw <= 480 && (r.height < 24 || r.width < 24) && txt(el) && !stretched && !inlineInSentence)
        out.push({ kind: 'touch-target', el: desc(el), w: Math.round(r.width), h: Math.round(r.height), text: txt(el) });
      for (const b of seenBoxes) {
        const ov = Math.max(0, Math.min(r.right, b.r.right) - Math.max(r.left, b.r.left)) *
                   Math.max(0, Math.min(r.bottom, b.r.bottom) - Math.max(r.top, b.r.top));
        if (ov > 16 && !el.contains(b.el) && !b.el.contains(el))
          out.push({ kind: 'overlap', el: desc(el), other: desc(b.el), area: Math.round(ov) });
      }
      seenBoxes.push({ r, el });
      // 6. a control with no accessible name — a wrapping <label>, a <label for>, or aria-labelledby all give one
      const labelled = el.closest('label') || el.getAttribute('aria-labelledby') ||
        (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'));
      if (!txt(el) && !el.getAttribute('aria-label') && !el.getAttribute('title') && !labelled)
        out.push({ kind: 'unnamed-control', el: desc(el) });
    }
  });
  return out;
}"""


def pages() -> list[str]:
    inc = next((i for i in db.incidents.list(limit=400) if i.diagnosis), db.incidents.list(limit=1)[0])
    return ["/", "/incidents", "/approvals", "/jobs", "/jobs/vision-7b", "/jobs/launch", "/fleet", "/budget",
            "/policies", "/audit", "/system", "/ask"] + \
           [f"/incidents/{inc.incident_id}{t}" for t in ("", "?tab=timeline", "?tab=decisions", "?tab=evidence")]


def main() -> int:
    env = dict(os.environ)
    logs = open(OUT / "lint_servers.log", "w")
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

    report: dict = {}
    counts: dict[str, int] = {}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            for tag, vw, vh in (("desktop", 1440, 900), ("phone", 390, 844)):
                ctx = b.new_context(viewport={"width": vw, "height": vh}, timezone_id="Asia/Jakarta")
                pg = ctx.new_page()
                for path in pages():
                    pg.goto(UI + path, wait_until="load", timeout=60000)
                    pg.wait_for_timeout(400)
                    found = pg.evaluate(LINT, vw)
                    if found:
                        report[f"{tag} {path}"] = found
                        for f in found:
                            counts[f["kind"]] = counts.get(f["kind"], 0) + 1
                    print(f"  {'OK ' if not found else f'{len(found):3d}'}  {tag:8s} {path}", flush=True)
                ctx.close()
            b.close()
    finally:
        core.terminate(); ui.terminate()
        (OUT / "lint_report.json").write_text(json.dumps(report, indent=1))
        total = sum(counts.values())
        print(f"\n{total} findings" + (f" — {counts}" if counts else " — clean"))
        for page, items in report.items():
            kinds = {}
            for i in items:
                kinds.setdefault(i["kind"], []).append(i)
            print(f"\n{page}")
            for k, v in kinds.items():
                print(f"  {k} ×{len(v)}")
                for i in v[:4]:
                    print(f"    {json.dumps({x: y for x, y in i.items() if x != 'kind'}, ensure_ascii=False)[:150]}")
        print("\nreport →", OUT / "lint_report.json")
    return 0 if not report else 1


if __name__ == "__main__":
    raise SystemExit(main())
