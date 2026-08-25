"""warden-ui v2: FastAPI + Jinja2 + the design-system stylesheet. Server renders UTC; the browser localizes time. Actions are signed here and forwarded to warden-core."""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from warden.signals.ingest import sign
from warden.ui2 import data

CORE = os.environ.get("WARDEN_CORE_URL", "http://127.0.0.1:18090")
HERE = Path(__file__).parent
app = FastAPI(title="Warden UI")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
tpl = Jinja2Templates(directory=str(HERE / "templates"))


def render(request: Request, name: str, page: str, title: str, ctx: dict, **extra) -> HTMLResponse:
    return tpl.TemplateResponse(request, name, {"page": page, "title": title, **ctx, **extra})


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/act")
async def act(request: Request):
    body = await request.json()
    path, key = str(body.get("path", "")), str(body.get("key", ""))
    if not path.startswith(("/decisions/", "/freeze", "/steward", "/jobs/")):
        return JSONResponse({"ok": False, "error": "unknown action"}, status_code=400)
    try:
        r = httpx.post(f"{CORE}{path}", headers={"X-Warden-Signature": sign(key.encode())}, json=body.get("json") or None, timeout=60)
        try:
            return r.json()
        except ValueError:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:160]}


@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    return render(request, "overview.html", "overview", "Overview", data.overview_context())


@app.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident(request: Request, incident_id: str, tab: str = "overview"):
    ctx = data.incident_context(incident_id)
    if ctx is None:
        return HTMLResponse("<p>Incident not found.</p>", status_code=404)
    return render(request, "incident.html", "incidents", ctx["inc"]["title"], ctx, tab=tab, main_class="incident", refresh=0)


@app.get("/incidents", response_class=HTMLResponse)
def incidents(request: Request):
    ctx = data.base_context()
    return render(request, "incidents.html", "incidents", "Incidents", ctx, rows=[data.incident_row(i) for i in ctx["incs"][:200]])


@app.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request):
    ctx = data.overview_context()
    stale = [data.decision_view(d, next((i for i in ctx["incs"] if i.incident_id == d.incident_id), None)) for d in ctx["decs"] if data._s(d.status) in ("EXPIRED", "REJECTED", "FAILED")][:10]
    from datetime import datetime, timezone
    ov = [x.to_dict() | {"id": x.id} for x in data.db.client().collection("policy_overrides").stream()]
    for o in ov:
        o["until_iso"] = datetime.fromtimestamp(float(o.get("until", 0)), tz=timezone.utc).isoformat()
    return render(request, "approvals.html", "approvals", "Approvals", ctx, stale=stale, overrides=ov)


@app.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request):
    return render(request, "jobs.html", "jobs", "Jobs", data.overview_context())


@app.get("/jobs/launch", response_class=HTMLResponse)
def launch_get(request: Request):
    return render(request, "launch.html", "jobs", "Launch job", data.base_context(), refresh=0)


@app.post("/jobs/launch", response_class=HTMLResponse)
def launch_post(request: Request, job_id: str = Form(""), command: str = Form(""), machine_type: str = Form("e2-medium"), zones: str = Form("us-central1-a,us-central1-b,us-central1-c"),
                spot: str = Form("on"), disk_gb: int = Form(20), budget_cap_usd: float = Form(0.0), expect: str = Form("{}"), entry: str = Form("")):
    import json as _json
    ctx = data.base_context(); result = None; error = ""
    try:
        spec = {"job_id": job_id.strip(), "command": command.strip(), "machine_type": machine_type.strip(), "zones": [z.strip() for z in zones.split(",") if z.strip()],
                "spot": spot == "on", "disk_gb": int(disk_gb), "budget_cap_usd": float(budget_cap_usd), "expect": _json.loads(expect or "{}"), "entry": entry.strip()}
        body = _json.dumps(spec).encode()
        r = httpx.post(f"{CORE}/jobs/launch", content=body, headers={"X-Warden-Signature": sign(body), "Content-Type": "application/json"}, timeout=400)
        result = r.json()
        if not result.get("ok"):
            error = str(result.get("error") or result.get("detail") or f"HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        error = str(e)[:200]
    return render(request, "launch.html", "jobs", "Launch job", ctx, result=result, error=error, form=locals(), refresh=0)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str):
    ctx = data.job_context(job_id)
    if ctx is None:
        return HTMLResponse("<p>Job not found.</p>", status_code=404)
    from warden.core.models import Action
    actions = [(a.value, data.label(data.ACTION_LABEL, a.value)) for a in Action if a.value != "notify"]
    return render(request, "job.html", "jobs", job_id, ctx, actions=actions)


@app.get("/fleet", response_class=HTMLResponse)
def fleet(request: Request):
    ctx = data.base_context()
    rows = []
    for i in sorted(data.db.fleet.list(limit=200), key=lambda x: x.ref):
        h = data.db.last_heartbeat(i.job_id) if i.job_id else None; txt, cls, ts = data.hb_state(h)
        rows.append({"i": i, "hb_text": txt, "hb_cls": cls, "hb_iso": ts, "h": h, "status": data._s(i.status), "seen_iso": data.iso(i.last_seen), "price": data.usd(i.hourly_price_usd, 3),
                     "unsafe": i.termination_action == "DELETE" or bool(i.boot_disk_auto_delete)})
    return render(request, "fleet.html", "fleet", "Fleet", ctx, rows=rows)


@app.get("/budget", response_class=HTMLResponse)
def budget(request: Request):
    ctx = data.overview_context()
    days = sorted((d.to_dict() | {"day": d.id} for d in data.db.client().collection("costs").stream()), key=lambda x: x["day"])
    cum, run = [], 0.0
    for d in days:
        run += float(d.get("compute_usd", 0.0)) + float(d.get("llm_usd", 0.0)); cum.append(run)
    cap = ctx["proj"]["cap_usd"]; n = len(cum)
    pts = " ".join(f"{40 + i * 580 / max(n - 1, 1):.0f},{110 - 80 * min(c, cap) / cap:.0f}" for i, c in enumerate(cum)) if n else ""
    return render(request, "budget.html", "budget", "Budget", ctx, days=days, chart_points=pts, labels=[d["day"][5:] for d in days])


@app.get("/policies", response_class=HTMLResponse)
def policies(request: Request):
    from warden.policy.engine import load_policy
    pol = load_policy()
    level_text = {"L0": "L0 · Observe", "L1": "L1 · Propose, approval required", "L2": "L2 · Act, then report", "L3": "L3 · Act silently"}
    rows = [{"action": data.label(data.ACTION_LABEL, a), "level": level_text.get(str(l), str(l)), "cls": "ok" if str(l) in ("L2", "L3") else "warn" if str(l) == "L1" else "muted",
             "limits": ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in (pol["limits"].get(a, {}) or {}).items()) or "—"} for a, l in pol["autonomy"].items()]
    return render(request, "policies.html", "policies", "Policies", data.base_context(), rows=rows, pol=pol, hard=[data.label(data.ACTION_LABEL, x) for x in pol["hard_deny"]])


@app.get("/audit", response_class=HTMLResponse)
def audit(request: Request):
    rows = [d.to_dict() for d in data.db.client().collection("audit").order_by("ts", direction="DESCENDING").limit(300).stream()]
    for r in rows:
        r["actor_label"] = "Operator" if str(r.get("actor", "")).startswith("human") else str(r.get("actor", "")).capitalize()
        r["action_label"] = data.label(data.ACTION_LABEL, r.get("action", "")); ok = r.get("ok")
        r["result"] = "—" if ok is None else (str((r.get("after") or {}).get("observed") or "Done")[:60] if ok else "Failed · " + str(r.get("error", ""))[:60])
        r["result_cls"] = "" if ok is None else ("ok" if ok else "crit")
    return render(request, "audit.html", "audit", "Audit Log", data.base_context(), rows=rows)


@app.get("/ask", response_class=HTMLResponse)
def ask_get(request: Request):
    ctx = data.base_context()
    return render(request, "ask.html", "ask", "Ask Warden", ctx, jobs=data.db.jobs.list(limit=100), refresh=0)


@app.post("/ask", response_class=HTMLResponse)
async def ask_post(request: Request, question: str = Form(""), job_id: str = Form(""), photo: UploadFile | None = File(default=None)):
    import base64 as _b64
    ctx = data.base_context()
    q = question.strip()[:1000]
    answer = None; error = ""
    payload = {"question": q, "job_id": job_id}
    if photo is not None and photo.filename:
        raw = await photo.read()
        if raw:
            payload["image_b64"] = _b64.b64encode(raw[:6_000_000]).decode(); payload["image_mime"] = photo.content_type or "image/jpeg"
    if q:
        try:
            r = httpx.post(f"{CORE}/ask", json=payload, headers={"X-Warden-Signature": sign(q.encode())}, timeout=180)
            j = r.json()
            if j.get("ok"):
                answer = j
            else:
                error = str(j.get("error") or j.get("detail") or f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            error = str(e)[:200]
    return render(request, "ask.html", "ask", "Ask Warden", ctx, jobs=data.db.jobs.list(limit=100), question=q, job_id=job_id, answer=answer, error=error, refresh=0)


@app.get("/system", response_class=HTMLResponse)
def health_page(request: Request):
    ctx = data.base_context()
    return render(request, "health.html", "health", "System Health", ctx)


def run():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), log_level="warning")


if __name__ == "__main__":
    run()
