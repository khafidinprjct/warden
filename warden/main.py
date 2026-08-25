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


_ALLOWED_SA = {f"warden-scheduler@{settings.project}.iam.gserviceaccount.com", f"warden-core@{settings.project}.iam.gserviceaccount.com"}
_OWNER = {e.strip() for e in os.getenv("WARDEN_OWNER_EMAILS", "inyongkhafid@gmail.com").split(",")}
_RATE: dict[str, list[float]] = {}


def _oidc_ok(auth: str | None) -> bool:
    """Produksi (Fase 12): token OIDC Google diverifikasi — tanda tangan, kedaluwarsa, audience = URL layanan ini,
    email = service account yang diizinkan (scheduler/core). Pemilik project (akun manusia) juga diterima untuk uji manual.
    Lokal: lewat bila WARDEN_DEV=1."""
    if os.getenv("WARDEN_DEV") == "1":
        return True
    if not auth or not auth.startswith("Bearer "):
        return False
    try:
        from google.auth.transport import requests as greq
        from google.oauth2 import id_token
        tok = auth.split(" ", 1)[1]; aud = os.getenv("WARDEN_SELF_URL")
        try:
            info = id_token.verify_oauth2_token(tok, greq.Request(), audience=aud) if aud else id_token.verify_oauth2_token(tok, greq.Request())
        except ValueError:
            info = id_token.verify_oauth2_token(tok, greq.Request())      # token manusia (gcloud) punya audience lain
            if info.get("email", "") not in _OWNER:
                return False
        email = info.get("email", "")
        return email in _ALLOWED_SA or (email in _OWNER and info.get("email_verified", False))
    except Exception:
        return False


def _rate_ok(key: str, limit: int = 120, window_s: int = 60) -> bool:
    import time as _t
    q = _RATE.setdefault(key, []); t = _t.time()
    while q and q[0] < t - window_s:
        q.pop(0)
    if len(q) >= limit:
        return False
    q.append(t); return True


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
    from warden.executor.recovery import process_verifying
    stats["recovery"] = process_verifying(notify=_notify)
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
    if verb == "always":
        return approvals.always(decision_id, who)
    if verb == "reevaluate":
        return approvals.reevaluate(decision_id, who)
    raise HTTPException(400, "verb: approve|deny|always|reevaluate")


@app.post("/ask")
async def ask(req: Request, x_warden_signature: str | None = Header(default=None)):
    """Concierge: operator question → Gemini with read-only tools + incident memory. HMAC over the question text (dashboard)."""
    body = await req.json()
    q = str(body.get("question", ""))[:1000]
    if not ing.verify(q.encode(), x_warden_signature or "") and os.getenv("WARDEN_DEV") != "1":
        raise HTTPException(401, "HMAC salah")
    if not q.strip():
        raise HTTPException(400, "question required")
    from warden.agents.concierge import ask as _ask
    try:
        return _ask(q, job_id=str(body.get("job_id", "")), incident_id=str(body.get("incident_id", "")))
    except Exception as e:  # noqa: BLE001
        db.health("gemini", False, str(e)[:200])
        return {"ok": False, "error": str(e)[:200]}


@app.post("/freeze")
def freeze(on: bool = True, who: str = "dashboard", x_warden_signature: str | None = Header(default=None)):
    if not ing.verify(b"freeze", x_warden_signature or "") and os.getenv("WARDEN_DEV") != "1":
        raise HTTPException(401, "HMAC salah")
    return approvals.freeze(who, on)


@app.post("/ingest/heartbeat")
async def ingest_heartbeat(req: Request, x_warden_signature: str | None = Header(default=None)):
    body = await req.body()
    if not _rate_ok("ingest:" + (req.client.host if req.client else "?")):
        raise HTTPException(429, "terlalu sering")
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


@app.post("/ingest/cmd_result")
async def ingest_cmd_result(req: Request, x_warden_signature: str | None = Header(default=None)):
    """The harness reports what happened to a mailbox command (nonce, ok, detail, freed_bytes…) — the verifier reads it."""
    body = await req.body()
    if not ing.verify(body, x_warden_signature or ""):
        raise HTTPException(401, "HMAC salah")
    res = json.loads(body)
    db.cmd_result_put(str(res.get("job_id", "")), res)
    db.audit(__import__("warden.core.models", fromlist=["AuditEntry"]).AuditEntry(actor="harness", phase="result", action=str(res.get("cmd", "")), target=str(res.get("job_id", "")),
             decision_id=str(res.get("decision_id", "")), after={k: v for k, v in res.items() if k not in ("job_id", "cmd", "decision_id")}, ok=bool(res.get("ok")), error=str(res.get("detail", "") if not res.get("ok") else "")))
    return {"ok": True}


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
async def events(req: Request, authorization: str | None = Header(default=None)):
    """Pub/Sub push (warden-events), OIDC dari service account push (Fase 12)."""
    if not _oidc_ok(authorization):
        raise HTTPException(401, "OIDC diperlukan")
    msg = (await req.json()).get("message", {})
    data = json.loads(base64.b64decode(msg.get("data", "e30=")).decode() or "{}")
    return {"ok": True, "received": data.get("kind", "?")}


@app.post("/steward")
def steward(authorization: str | None = Header(default=None)):
    if not _oidc_ok(authorization):
        raise HTTPException(401, "OIDC diperlukan")
    from warden.steward import ledger
    from warden.agents import memory
    out = {"accrue": ledger.accrue(600), "projection": ledger.projection(), "overrides_expired": ledger.expire_overrides(),
           "promotion_candidates": ledger.promotion_candidates(), "postmortems_written": memory.write_postmortems()}
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
async def budget(req: Request, authorization: str | None = Header(default=None)):
    """Billing budget → Pub/Sub push (OIDC) → sini. Ambang 0,5/0,8/1,0 (Fase 6 mengaktifkan kill-switch)."""
    if not _oidc_ok(authorization):
        raise HTTPException(401, "OIDC diperlukan")
    msg = (await req.json()).get("message", {})
    data = json.loads(base64.b64decode(msg.get("data", "e30=")).decode() or "{}")
    pct = float(data.get("alertThresholdExceeded", 0) or 0)
    from warden.steward import ledger
    out = ledger.budget_kill_switch(pct, notify=_notify)
    db.client().collection("policies").document("runtime").set({"budget_at": now().isoformat()}, merge=True)
    return {"ok": True, **out}


@app.post("/discord/interactions")
async def discord_interactions(req: Request, x_signature_ed25519: str | None = Header(default=None), x_signature_timestamp: str | None = Header(default=None)):
    from warden.concierge import discord as dc
    body = await req.body()
    if not settings.discord_public_key or not dc.verify_signature(settings.discord_public_key, x_signature_ed25519 or "", x_signature_timestamp or "", body):
        raise HTTPException(401, "tanda tangan Discord tidak sah")
    return dc.handle_interaction(json.loads(body))


def _notify(inc, dec, text: str) -> None:
    """Kartu Discord (Fase 7) + salinan ke koleksi notifications (dashboard)."""
    from warden.concierge import discord as dc
    try:
        db.client().collection("notifications").document(f"{getattr(inc, 'incident_id', 'x')}:{now().strftime('%H%M%S%f')}").set(
            {"incident_id": getattr(inc, "incident_id", ""), "decision_id": dec.decision_id if dec else "", "text": text, "ts": now().isoformat()})
    except Exception as e:  # noqa: BLE001 — notification copy must never block an action
        db.health("notifications", False, str(e)[:200])
    try:
        dc.send(inc if hasattr(inc, "rule") else None, dec, text)
        db.health("discord", True)
    except Exception as e:  # noqa: BLE001 — Discord down: action already taken; degrade to health record
        db.health("discord", False, str(e)[:200])
