"""Concierge Discord (Fase 7): kartu insiden (embed + tombol), interaksi (Ed25519, ack ≤3 s), perintah /warden.
Persetujuan hidup di Firestore (decisions) — tombol hanya memanggil approvals.approve/deny."""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

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
    fields = [{"name": "job / mesin", "value": f"{inc.job_id or '-'} · {inc.instance_ref or '-'}", "inline": True},
              {"name": "biaya terbakar", "value": f"${inc.cost_burning_usd_per_hour:.3f}/jam", "inline": True},
              {"name": "status", "value": str(inc.state), "inline": True}]
    if inc.diagnosis:
        d = inc.diagnosis
        cc = inc.crosscheck or {}
        fields.append({"name": "diagnosis", "value": f"{d.get('category')} · conf {cc.get('adjusted_confidence', d.get('confidence', 0)):.2f} · "
                                                     f"{d.get('transient_or_permanent')} · cek silang {'✅' if cc.get('passed') else '❌'}"[:1000]})
        if d.get("evidence_quotes"):
            fields.append({"name": "bukti", "value": "```\n" + "\n".join(q[:150] for q in d["evidence_quotes"][:8])[:900] + "\n```"})
        if d.get("falsifiable_check"):
            fields.append({"name": "cara membantah", "value": d["falsifiable_check"][:300]})
    if dec:
        fields.append({"name": "usulan", "value": f"**{dec.action}** · {dec.autonomy} · {dec.verdict} · blast radius {dec.blast_radius} · biaya ${dec.cost_usd:.2f}"})
        if dec.dry_run_plan.get("plan"):
            fields.append({"name": "rencana (dry-run)", "value": "```json\n" + json.dumps(dec.dry_run_plan["plan"], ensure_ascii=False)[:800] + "\n```"})
        if dec.explain:
            fields.append({"name": "aturan", "value": "\n".join(f"• {e}" for e in dec.explain[-5:])[:1000]})
    embed = {"title": f"WARDEN · {inc.rule} · {inc.instance_ref or inc.job_id}"[:250], "description": text[:2000],
             "color": _color(inc.severity), "fields": fields,
             "footer": {"text": f"{inc.incident_id} · {now():%d %b %H:%M} UTC" + (f" · kedaluwarsa {dec.expires_at:%H:%M} UTC" if dec and dec.expires_at else "")}}
    payload: dict[str, Any] = {"embeds": [embed]}
    if dec and dec.verdict == "NEED_APPROVAL" and dec.status == "PENDING":
        payload["components"] = [{"type": 1, "components": [
            {"type": 2, "style": STYLE["approve"], "label": "Approve", "custom_id": f"warden:approve:{dec.decision_id}"},
            {"type": 2, "style": STYLE["deny"], "label": "Deny", "custom_id": f"warden:deny:{dec.decision_id}"},
            {"type": 2, "style": STYLE["always"], "label": "Always 24h", "custom_id": f"warden:always:{dec.decision_id}"}]}]
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
        return {"type": 4, "data": {"content": f"⛔ {uname} tidak terdaftar sebagai approver.", "flags": 64}}
    if t == 3:
        cid = body.get("data", {}).get("custom_id", "")
        try:
            _, verb, decision_id = cid.split(":", 2)
        except ValueError:
            return {"type": 4, "data": {"content": "custom_id tidak dikenal", "flags": 64}}
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
        else:
            r = {"ok": False, "error": "verb"}
        status = "✅ disetujui & dijalankan" if r.get("ok") and verb != "deny" else ("🚫 ditolak" if verb == "deny" else f"❌ {r.get('error')}")
        # tipe 7 = UPDATE_MESSAGE: kartu asli diperbarui, tombol dinonaktifkan (klik ganda idempoten)
        return {"type": 7, "data": {"content": f"{status} oleh {uname} · {r.get('observed', '')}"[:1900], "components": []}}
    if t == 2:
        name = body.get("data", {}).get("name"); opts = {o["name"]: o.get("value") for o in body.get("data", {}).get("options", [])}
        sub = opts.get("perintah", "") or (body.get("data", {}).get("options") or [{}])[0].get("name", "")
        return {"type": 4, "data": {"content": slash(sub or name, opts, uname)[:1900]}}
    return {"type": 4, "data": {"content": "tipe interaksi tidak didukung", "flags": 64}}


def slash(cmd: str, opts: dict[str, Any], who: str) -> str:
    from warden.steward import ledger
    if cmd in ("freeze", "thaw"):
        approvals.freeze(f"discord:{who}", cmd == "freeze"); return f"{'🧊 DIBEKUKAN' if cmd == 'freeze' else '🔥 dilepas'} oleh {who}"
    if cmd == "hold":
        job = db.jobs.get(str(opts.get("job", "")))
        if not job:
            return "job tidak ada"
        from datetime import timedelta
        job.operator_hold_until = now() + timedelta(hours=float(opts.get("jam", 2))); db.jobs.put(job)
        return f"⏸ {job.job_id} ditahan sampai {job.operator_hold_until:%H:%M} UTC"
    if cmd == "status":
        return ledger.digest()
    if cmd == "why":
        incs = [i for i in db.incidents.list(job_id=str(opts.get("job", "")), limit=50)]
        if not incs:
            return "tidak ada insiden"
        i = sorted(incs, key=lambda x: x.created_at)[-1]
        d = i.diagnosis or {}
        return f"{i.rule} · {i.state}\n{d.get('human_summary_id') or i.summary}\nbukti: {d.get('evidence_quotes', [])[:3]}"
    return f"perintah: freeze | thaw | hold job jam | status | why job"
