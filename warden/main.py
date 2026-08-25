"""warden-core: FastAPI. Endpoint: /healthz, /tick (Scheduler, OIDC), /ingest (harness, HMAC), /events (Pub/Sub push),
/budget (billing alerts), /cmd/{job} (mailbox harness), /discord/interactions (Fase 7)."""
from __future__ import annotations

import base64
import json
import os

from fastapi import FastAPI, Header, HTTPException, Request

from warden.config import settings
from warden.core.models import now
from warden.signals import ingest as ing
from warden.store import firestore as db
from warden.executor import approvals
from warden.watcher.tick import run_tick

app = FastAPI(title="warden-core", version="0.1.0")


def _oidc_ok(auth: str | None) -> bool:
    """Produksi: Cloud Run + Scheduler memakai OIDC; verifikasi audience di Fase 12 (IAP/ingress internal).
    Lokal: lewat bila WARDEN_DEV=1."""
    if os.getenv("WARDEN_DEV") == "1":
        return True
    return bool(auth and auth.startswith("Bearer "))


@app.get("/health")
@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": now().isoformat(), "provider": settings.provider, "project": settings.project}


@app.post("/tick")
def tick(authorization: str | None = Header(default=None)):
    if not _oidc_ok(authorization):
        raise HTTPException(401, "OIDC diperlukan")
    approvals.expire_stale()
    stats = run_tick(notify=_notify)
    from warden.agents.pipeline import process_diagnosing
    stats["llm"] = process_diagnosing(notify=_notify)
    from warden.verifier.run import process_pending
    stats["verify"] = process_pending(notify=_notify)
    return stats


@app.post("/decisions/{decision_id}/{verb}")
def decide(decision_id: str, verb: str, who: str = "dashboard", x_warden_signature: str | None = Header(default=None)):
    """Dipakai dashboard (Fase 8) & Discord (Fase 7). HMAC atas decision_id."""
    if not ing.verify(decision_id.encode(), x_warden_signature or "") and os.getenv("WARDEN_DEV") != "1":
        raise HTTPException(401, "HMAC salah")
    if verb == "approve":
        return approvals.approve(decision_id, who)
    if verb == "deny":
        return approvals.deny(decision_id, who)
    raise HTTPException(400, "verb: approve|deny")


@app.post("/freeze")
def freeze(on: bool = True, who: str = "dashboard", x_warden_signature: str | None = Header(default=None)):
    if not ing.verify(b"freeze", x_warden_signature or "") and os.getenv("WARDEN_DEV") != "1":
        raise HTTPException(401, "HMAC salah")
    return approvals.freeze(who, on)


@app.post("/ingest/heartbeat")
async def ingest_heartbeat(req: Request, x_warden_signature: str | None = Header(default=None)):
    body = await req.body()
    if not ing.verify(body, x_warden_signature or ""):
        raise HTTPException(401, "HMAC salah")
    hb = ing.ingest_heartbeat(json.loads(body))
    return {"ok": True, "job_id": hb.job_id, "ts": hb.ts.isoformat()}


@app.post("/ingest/marker")
async def ingest_marker(req: Request, x_warden_signature: str | None = Header(default=None)):
    body = await req.body()
    if not ing.verify(body, x_warden_signature or ""):
        raise HTTPException(401, "HMAC salah")
    mk = ing.ingest_marker(json.loads(body))
    return {"ok": True, "valid": mk.valid, "reason": mk.invalid_reason}


@app.get("/cmd/{job_id}")
def mailbox(job_id: str, x_warden_signature: str | None = Header(default=None)):
    """Harness mem-poll perintah: {cmd, args}. Perintah dihapus setelah diambil (sekali pakai)."""
    if not ing.verify(job_id.encode(), x_warden_signature or ""):
        raise HTTPException(401, "HMAC salah")
    ref = db.client().collection("cmd").document(job_id)
    d = ref.get()
    if not d.exists:
        return {"cmd": None}
    ref.delete()
    return d.to_dict()


@app.post("/events")
async def events(req: Request):
    """Pub/Sub push (warden-events). Fase 4: insiden needs_llm diproses di sini."""
    msg = (await req.json()).get("message", {})
    data = json.loads(base64.b64decode(msg.get("data", "e30=")).decode() or "{}")
    return {"ok": True, "received": data.get("kind", "?")}


@app.post("/steward")
def steward(authorization: str | None = Header(default=None)):
    if not _oidc_ok(authorization):
        raise HTTPException(401, "OIDC diperlukan")
    from warden.steward import ledger
    out = {"accrue": ledger.accrue(600), "projection": ledger.projection()}
    db.heartbeat_self("steward", out)
    return out


@app.post("/digest")
def digest(authorization: str | None = Header(default=None)):
    if not _oidc_ok(authorization):
        raise HTTPException(401, "OIDC diperlukan")
    from warden.steward import ledger
    text = ledger.digest()
    _notify(type("I", (), {"incident_id": "digest"})(), None, text)
    return {"ok": True, "text": text}


@app.post("/budget")
async def budget(req: Request):
    """Billing budget → Pub/Sub → sini. Ambang 0,5/0,8/1,0 (Fase 6 mengaktifkan kill-switch)."""
    msg = (await req.json()).get("message", {})
    data = json.loads(base64.b64decode(msg.get("data", "e30=")).decode() or "{}")
    pct = float(data.get("alertThresholdExceeded", 0) or 0)
    from warden.steward import ledger
    out = ledger.budget_kill_switch(pct, notify=_notify)
    db.client().collection("policies").document("runtime").set({"budget_at": now().isoformat()}, merge=True)
    return {"ok": True, **out}


def _notify(inc, dec, text: str) -> None:
    """Fase 7 mengganti ini dengan kartu Discord; sekarang: catat ke koleksi notifications (terlihat di dashboard)."""
    db.client().collection("notifications").document(f"{inc.incident_id}:{now().strftime('%H%M%S%f')}").set(
        {"incident_id": inc.incident_id, "decision_id": dec.decision_id if dec else "", "text": text, "ts": now().isoformat()})
