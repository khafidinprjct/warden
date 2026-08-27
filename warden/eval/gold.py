"""Gold/silver evaluation of the Diagnostician (checklist C4/M3): real logs with known causes.
Scores action accuracy (recommended_action ∈ allowed set), category accuracy, fabricated-evidence rate (evidence_lines out of range /
quotes not in the log), needs_human on the hidden-cause case, and cost. Run nightly (/eval) and on every prompt or model change."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import yaml

# The gold set ships inside the package: the nightly /eval runs in the Cloud Run image, where tests/ is not present
# (catalogue #36 — every attempt crashed with FileNotFoundError once authentication started working).
FIX = Path(__file__).resolve().parent / "cases"


def load_cases() -> tuple[float, list[dict]]:
    d = yaml.safe_load((FIX / "cases.yaml").read_text())
    return float(d.get("threshold", 0.9)), d["cases"]


def run(model: str | None = None, only: str = "", tail: int = 200) -> dict[str, Any]:
    from warden.agents.crosscheck import crosscheck
    from warden.agents.diagnostician import diagnose
    thr, cases = load_cases()
    rows: list[dict] = []; cost = 0.0
    for c in cases:
        if only and only not in c["file"]:
            continue
        lines = (FIX / c["file"]).read_text(errors="ignore").splitlines()[-tail:]
        t0 = time.time()
        try:
            diag, usage = diagnose({"job": c["file"].split(".")[0], "phase": "", "legacy": False}, [{"rule": "run_fin_nonzero", "summary": "run exited with error"}], {}, lines, model=model)
        except Exception as e:  # noqa: BLE001
            rows.append({"file": c["file"], "tier": c["tier"], "ok": False, "error": str(e)[:200]}); continue
        cc = crosscheck(diag, lines, None)
        fabricated = not all(x["ok"] for x in cc["checks"] if x["check"] in ("evidence_lines_in_range", "quotes_are_substrings"))
        cat_ok = str(diag.category) in c["category"]; act_ok = str(diag.recommended_action) in c["action"]
        human_ok = (diag.needs_human or cc["needs_human"]) if c.get("needs_human") else True
        culprit_ok = (c["culprit"] in (diag.culprit_frame or "").lower()) if c.get("culprit") else True
        cost += usage.get("cost_usd", 0.0)
        rows.append({"file": c["file"], "tier": c["tier"], "ok": cat_ok and act_ok and not fabricated and human_ok and culprit_ok,
                     "category": str(diag.category), "category_ok": cat_ok, "action": str(diag.recommended_action), "action_ok": act_ok,
                     "fabricated_evidence": fabricated, "needs_human": diag.needs_human, "human_ok": human_ok, "culprit_ok": culprit_ok,
                     "confidence": diag.confidence, "cc_passed": cc["passed"], "ms": int((time.time() - t0) * 1000), "cost_usd": usage.get("cost_usd", 0.0),
                     "root_cause": diag.root_cause[:160]})
    n = len(rows); ok = sum(1 for r in rows if r["ok"]); act = sum(1 for r in rows if r.get("action_ok"))
    rep = {"model": model or "default", "n": n, "passed": ok, "accuracy": round(ok / n, 3) if n else None, "action_accuracy": round(act / n, 3) if n else None,
           "fabricated": sum(1 for r in rows if r.get("fabricated_evidence")), "cost_usd": round(cost, 4), "threshold": thr,
           "meets_threshold": (ok / n >= thr) if n else False, "rows": rows, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return rep


def record(rep: dict[str, Any], notify=None) -> None:
    """Persist to Firestore (eval/<ts>) + health; below threshold → the human hears about it."""
    from warden.store import firestore as db
    db.client().collection("eval").document(rep["ts"].replace(":", "")).set({k: v for k, v in rep.items()})
    db.health("gold_eval", bool(rep["meets_threshold"]), "" if rep["meets_threshold"] else f"accuracy {rep['accuracy']} < {rep['threshold']}")
    if notify and not rep["meets_threshold"]:
        notify(None, None, f"📉 Diagnostician gold eval {rep['passed']}/{rep['n']} (accuracy {rep['accuracy']}, {rep['fabricated']} fabricated) — below {rep['threshold']}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--model", default=None); ap.add_argument("--only", default=""); ap.add_argument("--out", default="eval/gold_report.json")
    ns = ap.parse_args()
    rep = run(ns.model, ns.only)
    Path(ns.out).parent.mkdir(parents=True, exist_ok=True); Path(ns.out).write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    for r in rep["rows"]:
        print(("OK   " if r.get("ok") else "FAIL ") + f"{r['file']:<42} {r.get('category', '?'):<20} {r.get('action', '?'):<22} conf={r.get('confidence', '-')} fab={r.get('fabricated_evidence')} {r.get('error', '')}")
    print(f"\n{rep['passed']}/{rep['n']} accuracy={rep['accuracy']} action_accuracy={rep['action_accuracy']} fabricated={rep['fabricated']} cost=${rep['cost_usd']} → {ns.out}")
