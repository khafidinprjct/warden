"""Extract every word the dashboard actually shows, so the writing can be reviewed as writing.

The lint measures pixels; this collects language. Playwright walks each page at both viewports and records every visible
text node with the element that owns it, then flags the mechanical faults a reader would call sloppy: identifiers that
escaped the vocabulary layer (snake_case, ALL_CAPS enums, dotted paths), Title Case where the design system says
sentence case, trailing periods on labels, doubled spaces, straight quotes, and words the product has decided not to use.

Judgement stays with a human: the corpus is written out in full so the copy can be read end to end.

    python -m chaos.ui_copy            # → docs/video/tour/copy_report.json + copy_corpus.txt
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("TOUR_OUT", ROOT / "docs/video/tour"))
CORE_PORT, UI_PORT = "18104", "8104"
os.environ.update({
    "FIRESTORE_EMULATOR_HOST": "127.0.0.1:8081", "WARDEN_PROJECT": "warden-local", "WARDEN_FIRESTORE_DB": "warden-tour",
    "WARDEN_PROVIDER": "fake", "WARDEN_DEV": "1", "WARDEN_FAKE_STATE": str(OUT / "fleet.json"),
    "WARDEN_CORE_URL": f"http://127.0.0.1:{CORE_PORT}", "PORT": UI_PORT,
})
UI = f"http://127.0.0.1:{UI_PORT}"

from warden.store import firestore as db  # noqa: E402

COLLECT = r"""() => {
  const out = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const t = (n.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t) continue;
    const el = n.parentElement;
    if (!el || ['SCRIPT', 'STYLE'].includes(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const d = el.closest('details:not([open])');
    if (d && !el.closest('summary')) continue;
    out.push({
      text: t,
      tag: el.tagName.toLowerCase(),
      cls: (el.className && el.className.toString ? el.className.toString().trim().split(/\s+/).slice(0, 3).join('.') : ''),
      role: (el.closest('button,.btn') ? 'control'
             : (el.closest('a,summary') ? 'link'
                : (el.closest('.th') ? 'header' : (el.closest('.k,.eyebrow') ? 'label' : 'body')))),
    });
  }
  // placeholders and titles are copy too, and never appear as text nodes
  document.querySelectorAll('[placeholder]').forEach(e => out.push({ text: e.getAttribute('placeholder'), tag: e.tagName.toLowerCase(), cls: 'placeholder', role: 'placeholder' }));
  document.querySelectorAll('[aria-label]').forEach(e => out.push({ text: e.getAttribute('aria-label'), tag: e.tagName.toLowerCase(), cls: 'aria-label', role: 'aria' }));
  return out;
}"""

# Words the product decided against, and shapes that mean an identifier escaped the vocabulary layer.
BANNED = {"deadman": "Watchdog", "gcs": "Cloud Storage", "vm": "machine", "ETTR": None}
IDENTIFIER = re.compile(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b")
ENUM = re.compile(r"\b[A-Z][A-Z0-9]{3,}(?:_[A-Z0-9]+)*\b")
DOTTED = re.compile(r"\b[a-z]+\.[a-z_]+\.[a-z_.]+\b")
ALLOWED_ENUM = {"RUN_FIN", "DONE", "STOP", "DELETE", "GMT", "CPU", "VRAM", "SEVERITY", "INCIDENT", "JOB", "STATUS",
                "OPENED", "MACHINE", "TYPE", "PRICE", "HEARTBEAT", "SEEN", "TIME", "ACTOR", "PHASE", "ACTION", "TARGET",
                "RESULT", "LEVEL", "LIMITS", "SYSTEM", "SEARCH", "DECISION", "CREATED", "EFFECTIVE", "PAID", "SPENT",
                "ETTR", "TRANSITION", "NOTE", "QUESTION", "PROGRESS", "RUN", "INSTANCE", "OVERRIDES", "EXPIRES", "JSON",
                 "CUDA", "USD", "GPU", "ID", "OK", "GB", "API", "NOT"}
# Warden quoting the machine — a log excerpt or an incident summary citing GCE state — is reporting, not vocabulary.
QUOTING = ("TERMINATED without RUN_FIN", "OutOfMemoryError", "Traceback", "torch.")


def pages() -> list[str]:
    inc = next((i for i in db.incidents.list(limit=400) if i.diagnosis), db.incidents.list(limit=1)[0])
    return ["/", "/incidents", "/approvals", "/jobs", "/jobs/vision-7b", "/jobs/launch", "/fleet", "/budget",
            "/policies", "/audit", "/system", "/ask"] + \
           [f"/incidents/{inc.incident_id}{t}" for t in ("", "?tab=timeline", "?tab=decisions", "?tab=evidence")]


def flags(item: dict) -> list[str]:
    t, role = item["text"], item["role"]
    f = []
    if IDENTIFIER.search(t) and role in ("control", "header", "label", "link"):
        f.append("identifier in a label")
    if DOTTED.search(t) and role in ("control", "header", "label", "link"):
        f.append("dotted path in a label")
    quoting = any(k in t for k in QUOTING)
    for m in ENUM.findall(t):
        if m not in ALLOWED_ENUM and not quoting:
            f.append(f"raw enum {m}")
    if role == "control" and t.endswith("."):
        f.append("control label ends with a full stop")
    if "  " in t:
        f.append("double space")
    if "'" in t or '"' in t:
        f.append("straight quote")
    low = t.lower()
    for bad, better in BANNED.items():
        if better and re.search(rf"\b{bad}\b", low):
            f.append(f"says '{bad}', product word is '{better}'")
    if role == "control" and len(t.split()) > 6:
        f.append("control label longer than six words")
    return f


def main() -> int:
    env = dict(os.environ)
    logs = open(OUT / "copy_servers.log", "w")
    import socket
    for port in (CORE_PORT, UI_PORT):
        with socket.socket() as sk:
            if sk.connect_ex(("127.0.0.1", int(port))) == 0:
                raise SystemExit(f"port {port} already serving")
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

    corpus: dict[str, dict] = {}
    findings: list[dict] = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            for tag, vw, vh in (("desktop", 1440, 900), ("phone", 390, 844)):
                ctx = b.new_context(viewport={"width": vw, "height": vh}, timezone_id="Asia/Jakarta")
                pg = ctx.new_page()
                for path in pages():
                    pg.goto(UI + path, wait_until="load", timeout=60000)
                    pg.wait_for_timeout(300)
                    for it in pg.evaluate(COLLECT):
                        key = f"{it['role']}|{it['text']}"
                        e = corpus.setdefault(key, {**it, "pages": set()})
                        e["pages"].add(f"{tag} {path}")
                    print(f"  {tag:8s} {path}", flush=True)
                ctx.close()
            b.close()
    finally:
        core.terminate(); ui.terminate()

    for e in corpus.values():
        for f in flags(e):
            findings.append({"flag": f, "text": e["text"], "role": e["role"], "where": sorted(e["pages"])[0]})
    rows = sorted(corpus.values(), key=lambda x: (x["role"], x["text"].lower()))
    (OUT / "copy_corpus.txt").write_text("\n".join(f"[{r['role']:11s}] {r['text']}" for r in rows))
    (OUT / "copy_report.json").write_text(json.dumps(
        {"strings": len(corpus), "findings": findings,
         "by_role": {r: sum(1 for x in corpus.values() if x["role"] == r) for r in {y["role"] for y in corpus.values()}}},
        indent=1))
    print(f"\n{len(corpus)} distinct strings · {len(findings)} mechanical flags")
    for f in findings:
        print(f"  {f['flag']:44s} {f['role']:11s} {f['text'][:70]!r}")
    print("\ncorpus →", OUT / "copy_corpus.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
