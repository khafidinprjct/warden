"""Multimodal (Fase 9): (a) render kurva loss/step dari denyut → PNG untuk kartu & Diagnostician;
(b) foto layar dari HP → Gemini membaca teks/angka → temuan sintetis berlabel (confidence ≤ 0,6; tidak memicu aksi)."""
from __future__ import annotations

import io
from typing import Any

from warden.store import firestore as db


def render_loss_curve(job_id: str, n: int = 200) -> bytes | None:
    hbs = [h for h in db.recent_heartbeats(job_id, n) if h.loss is not None or h.step is not None]
    if len(hbs) < 3:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax1 = plt.subplots(figsize=(6.4, 3.2), dpi=120)
    xs = [h.step if h.step is not None else i for i, h in enumerate(hbs)]
    ax1.plot(xs, [h.loss for h in hbs], color="#1e88e5", lw=1.5); ax1.set_xlabel("step"); ax1.set_ylabel("loss", color="#1e88e5")
    if any(h.grad_norm is not None for h in hbs):
        ax2 = ax1.twinx(); ax2.plot(xs, [h.grad_norm for h in hbs], color="#e53935", lw=1, alpha=0.7); ax2.set_ylabel("grad_norm", color="#e53935")
    ax1.set_title(f"{job_id} — {len(hbs)} denyut terakhir"); ax1.grid(alpha=0.3); fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig)
    return buf.getvalue()


PHOTO_PROMPT = ("Ini foto layar (terminal/monitor) dari operator. Ekstrak: (1) pesan error atau peringatan yang terlihat, "
                "(2) angka penting (step, loss, VRAM, disk, exit code), (3) satu kalimat dugaan masalah. "
                "Jawab JSON: {\"errors\":[...], \"numbers\":{...}, \"guess\":\"...\", \"readable\": true|false}. Bila tidak terbaca, readable=false.")


def read_photo(image_bytes: bytes, mime: str = "image/jpeg") -> dict[str, Any]:
    """OCR + pemahaman ringan via Gemini 3.5 Flash. Keluaran = temuan sintetis: source=photo, confidence ≤ 0,6."""
    import json
    from google import genai
    from google.genai import types
    from warden.config import settings
    if len(image_bytes) > 4 * 1024 * 1024:
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(image_bytes)); im.thumbnail((1280, 1280)); b = io.BytesIO(); im.save(b, "JPEG", quality=85); image_bytes, mime = b.getvalue(), "image/jpeg"
        except Exception:
            pass
    client = genai.Client()
    r = client.models.generate_content(model=settings.gemini_model, contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime), PHOTO_PROMPT],
                                       config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1))
    try:
        d = json.loads(r.text)
    except Exception:
        d = {"errors": [], "numbers": {}, "guess": r.text[:200], "readable": False}
    d.update(source="photo", confidence=min(0.6, 0.6 if d.get("readable") else 0.2),
             tokens={"in": r.usage_metadata.prompt_token_count, "out": r.usage_metadata.candidates_token_count})
    return d
