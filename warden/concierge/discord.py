"""Concierge Discord (Fase 7): kartu insiden (embed + tombol), interaksi (Ed25519, ack ≤3 s), perintah /warden.
Persetujuan hidup di Firestore (decisions) — tombol hanya memanggil approvals.approve/deny."""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from google.cloud.firestore_v1 import FieldFilter

from warden.config import settings
from warden.core.models import Decision, Incident, now
from warden.executor import approvals
from warden.store import firestore as db

API = "https://discord.com/api/v10"
STYLE = {"approve": 3, "deny": 4, "always": 2}     # 3 success (hijau), 4 danger (merah), 2 secondary


def verify_signature(public_key_hex: str, signature: str, timestamp: str, body: bytes) -> bool:
    try:
        VerifyKey(bytes.fromhex(public_key_hex)).verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bot {settings.discord_bot_token}", "Content-Type": "application/json"}


def _color(sev: str) -> int:
    return {"critical": 0xE53935, "warning": 0xFDD835, "info": 0x43A047}.get(sev, 0x90A4AE)


def build_card(inc: Incident | None, dec: Decision | None, text: str) -> dict[str, Any]:
    """Embed: judul kelas·mesin, bukti ≤8 baris, diagnosis, usulan+risiko+biaya, kedaluwarsa; tombol bila menunggu izin."""
    if inc is None:
        return {"content": text[:1900]}
    fields = [{"name": "job / instance", "value": f"{inc.job_id or '-'} · {inc.instance_ref or '-'}", "inline": True},
              {"name": "burn rate", "value": f"${inc.cost_burning_usd_per_hour:.3f}/h", "inline": True},
              {"name": "status", "value": str(inc.state), "inline": True}]
    if inc.diagnosis:
        d = inc.diagnosis
        cc = inc.crosscheck or {}
        fields.append({"name": "diagnosis", "value": f"{d.get('category')} · conf {cc.get('adjusted_confidence', d.get('confidence', 0)):.2f} · "
                                                     f"{d.get('transient_or_permanent')} · crosscheck {'✅' if cc.get('passed') else '❌'}"[:1000]})
        if d.get("evidence_quotes"):
            fields.append({"name": "evidence", "value": "```\n" + "\n".join(q[:150] for q in d["evidence_quotes"][:8])[:900] + "\n```"})
        if d.get("falsifiable_check"):
            fields.append({"name": "how to falsify", "value": d["falsifiable_check"][:300]})
    if dec:
        fields.append({"name": "proposal", "value": f"**{dec.action}** · {dec.autonomy} · {dec.verdict} · blast radius {dec.blast_radius} · cost ${dec.cost_usd:.2f}"})
        if dec.dry_run_plan.get("plan"):
            fields.append({"name": "plan (dry-run)", "value": "```json\n" + json.dumps(dec.dry_run_plan["plan"], ensure_ascii=False)[:800] + "\n```"})
        if dec.explain:
            fields.append({"name": "policy", "value": "\n".join(f"• {e}" for e in dec.explain[-5:])[:1000]})
    embed = {"title": f"WARDEN · {inc.rule} · {inc.instance_ref or inc.job_id}"[:250], "description": text[:2000],
             "color": _color(inc.severity), "fields": fields,
             "footer": {"text": f"{inc.incident_id} · {now():%d %b %H:%M} UTC" + (f" · expires {dec.expires_at:%H:%M} UTC" if dec and dec.expires_at else "")}}
    payload: dict[str, Any] = {"embeds": [embed]}
    if dec and dec.verdict == "NEED_APPROVAL" and dec.status == "PENDING":
        payload["components"] = [{"type": 1, "components": [
            {"type": 2, "style": STYLE["approve"], "label": "Approve", "custom_id": f"warden:approve:{dec.decision_id}"},
            {"type": 2, "style": STYLE["deny"], "label": "Deny", "custom_id": f"warden:deny:{dec.decision_id}"},
            {"type": 2, "style": STYLE["always"], "label": "Always 24h", "custom_id": f"warden:always:{dec.decision_id}"}]}]
    elif dec and str(dec.status) == "EXPIRED":
        # the request lapsed while nobody was looking; the phone still offers the way back
        payload["components"] = [{"type": 1, "components": [
            {"type": 2, "style": STYLE["always"], "label": "Re-evaluate now",
             "custom_id": f"warden:reevaluate:{dec.decision_id}"}]}]
    return payload


def send(inc: Incident | None, dec: Decision | None, text: str) -> str:
    """Kirim kartu ke channel; simpan message id di decision (untuk edit setelah keputusan). Gagal = tercatat, tidak melempar."""
    if not settings.discord_bot_token or not settings.discord_channel_id:
        db.client().collection("notifications").document(f"{(inc.incident_id if inc else 'x')}:{now().strftime('%H%M%S%f')}").set(
            {"incident_id": inc.incident_id if inc else "", "decision_id": dec.decision_id if dec else "", "text": text, "ts": now().isoformat(), "channel": "none"})
        return ""
    try:
        r = httpx.post(f"{API}/channels/{settings.discord_channel_id}/messages", headers=_headers(), json=build_card(inc, dec, text), timeout=10)
        r.raise_for_status(); mid = r.json().get("id", "")
        if dec:
            dec.channel_msg_ref = f"{settings.discord_channel_id}/{mid}"; db.decisions.put(dec)
        db.health("discord", True)
        return mid
    except Exception as e:
        db.health("discord", False, str(e)[:200])
        return ""


def edit_message(msg_ref: str, payload: dict[str, Any]) -> None:
    if not msg_ref or not settings.discord_bot_token:
        return
    ch, mid = msg_ref.split("/", 1)
    try:
        httpx.patch(f"{API}/channels/{ch}/messages/{mid}", headers=_headers(), json=payload, timeout=10)
    except Exception as e:
        db.health("discord", False, str(e)[:200])


def handle_interaction(body: dict[str, Any]) -> dict[str, Any]:
    """Dipanggil endpoint setelah tanda tangan lolos. Tipe 1 = PING; 3 = tombol; 2 = slash command."""
    t = body.get("type")
    if t == 1:
        return {"type": 1}
    user = (body.get("member") or {}).get("user") or body.get("user") or {}
    uid, uname = str(user.get("id", "")), user.get("username", "?")
    approvers = [a.strip() for a in settings.approvers.split(",") if a.strip()]
    if approvers and uid not in approvers:
        return {"type": 4, "data": {"content": f"⛔ {uname} is not a registered approver.", "flags": 64}}
    if t == 3:
        cid = body.get("data", {}).get("custom_id", "")
        try:
            _, verb, decision_id = cid.split(":", 2)
        except ValueError:
            return {"type": 4, "data": {"content": "unknown custom_id", "flags": 64}}
        if verb == "approve":
            r = approvals.approve(decision_id, f"discord:{uname}")
        elif verb == "deny":
            r = approvals.deny(decision_id, f"discord:{uname}")
        elif verb == "always":
            r = approvals.approve(decision_id, f"discord:{uname}")
            dec = db.decisions.get(decision_id)
            if dec and dec.job_id:
                job = db.jobs.get(dec.job_id)
                if job:
                    job.autonomy_overrides[dec.action.value] = "L2"; db.jobs.put(job)   # 24 jam: dicatat, dilepas oleh digest (Fase 12: TTL)
                    db.client().collection("policy_overrides").document(f"{dec.job_id}:{dec.action.value}").set({"level": "L2", "until": (now().timestamp() + 86400), "by": uname})
        elif verb == "reevaluate":
            r = approvals.reevaluate(decision_id, f"discord:{uname}")
        else:
            r = {"ok": False, "error": "verb"}
        if verb == "reevaluate":
            status = (f"🔁 re-evaluated → {r.get('verdict', '?')}" if r.get("ok") else f"❌ {r.get('error')}")
        else:
            status = "✅ approved & executed" if r.get("ok") and verb != "deny" else ("🚫 denied" if verb == "deny" else f"❌ {r.get('error')}")
        # tipe 7 = UPDATE_MESSAGE: kartu asli diperbarui, tombol dinonaktifkan (klik ganda idempoten)
        return {"type": 7, "data": {"content": f"{status} by {uname} · {r.get('observed', '')}"[:1900], "components": []}}
    if t == 2:
        data = body.get("data", {})
        name = data.get("name")
        top = (data.get("options") or [{}])[0]
        sub = top.get("name", "") or ""
        opts = {o["name"]: o.get("value") for o in (top.get("options") or data.get("options") or [])}
        if sub == "ask":
            return _defer_ask(body, opts, uname)
        return {"type": 4, "data": {"content": slash(sub or name, opts, uname)[:1900]}}
    return {"type": 4, "data": {"content": "unsupported interaction type", "flags": 64}}


def _defer_ask(body: dict[str, Any], opts: dict[str, Any], who: str) -> dict[str, Any]:
    """Discord wants an answer in 3 seconds; the Concierge needs ten to thirty.

    So acknowledge immediately (type 5 = "Warden is thinking…") and park the question. The tick picks it up within a
    minute and posts the answer as a follow-up, which the interaction token accepts for fifteen minutes. No background
    thread on Cloud Run, where the CPU is throttled the moment the response is written.
    """
    att_url = ""
    ref = opts.get("image")
    if ref:
        att = ((body.get("data", {}).get("resolved") or {}).get("attachments") or {}).get(str(ref)) or {}
        att_url = str(att.get("url", ""))
    db.client().collection("discord_asks").document(str(body.get("id"))).set({
        "question": str(opts.get("question", ""))[:1000], "job_id": str(opts.get("job", "") or ""),
        "image_url": att_url, "token": body.get("token"), "who": who,
        "application_id": str(body.get("application_id", "")), "created_at": now().isoformat(), "state": "pending"})
    return {"type": 5}


def answer_pending_asks(limit: int = 3) -> dict[str, Any]:
    """Run from the tick: answer the questions asked with /warden ask and post them back to the interaction."""
    import httpx
    out = {"answered": 0, "failed": 0}
    for d in db.client().collection("discord_asks").where(filter=FieldFilter("state", "==", "pending")).limit(limit).get():
        rec = d.to_dict() or {}
        d.reference.set({"state": "working"}, merge=True)
        image = None
        try:
            if rec.get("image_url"):
                r = httpx.get(rec["image_url"], timeout=30)
                r.raise_for_status()
                image = r.content[:6_000_000]
            from warden.agents.concierge import ask
            res = ask(rec.get("question", ""), job_id=rec.get("job_id", ""), image=image,
                      image_mime="image/png" if not image else "image/jpeg" if image[:2] == b"\xff\xd8" else "image/png")
            text = res.get("answer", "") or "(no answer)"
            db.health("gemini", True)
            out["answered"] += 1
        except Exception as e:  # noqa: BLE001
            text = f"Could not answer: {type(e).__name__}: {e}"[:400]
            db.health("gemini", False, str(e)[:200])
            out["failed"] += 1
        body = {"content": (f"**{rec.get('question','')}**\n{text}")[:1900]}
        try:
            httpx.post(f"https://discord.com/api/v10/webhooks/{rec['application_id']}/{rec['token']}",
                       json=body, timeout=30).raise_for_status()
        except Exception as e:  # noqa: BLE001
            log_err = str(e)[:120]
            d.reference.set({"state": "failed", "error": log_err}, merge=True)
            continue
        d.reference.delete()
    return out


def slash(cmd: str, opts: dict[str, Any], who: str) -> str:
    from warden.steward import ledger
    if cmd in ("freeze", "thaw"):
        approvals.freeze(f"discord:{who}", cmd == "freeze"); return f"{'🧊 FROZEN' if cmd == 'freeze' else '🔥 thawed'} by {who}"
    if cmd == "hold":
        job = db.jobs.get(str(opts.get("job", "")))
        if not job:
            return "job not found"
        from datetime import timedelta
        job.operator_hold_until = now() + timedelta(hours=float(opts.get("hours", 2))); db.jobs.put(job)
        return f"⏸ {job.job_id} held until {job.operator_hold_until:%H:%M} UTC"
    if cmd == "status":
        return ledger.digest()
    if cmd == "why":
        incs = [i for i in db.incidents.list(job_id=str(opts.get("job", "")), limit=50)]
        if not incs:
            return "no incidents"
        i = sorted(incs, key=lambda x: x.created_at)[-1]
        d = i.diagnosis or {}
        return f"{i.rule} · {i.state}\n{d.get('human_summary') or d.get('human_summary_id') or i.summary}\nevidence: {d.get('evidence_quotes', [])[:3]}"
    return f"commands: freeze | thaw | hold <job> <hours> | status | why <job>"
