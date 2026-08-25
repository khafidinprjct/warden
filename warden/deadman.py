"""warden-deadman — layanan Cloud Run TERPISAH (service account sendiri, P5). Dipicu Scheduler tiap 5 menit:
kalau denyut watcher basi > 15 menit → STOP semua mesin warden-managed + catat + beri tahu. Tidak bergantung pada core."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI

from warden.config import settings
from warden.core.models import InstanceStatus
from warden.providers.registry import compute
from warden.store import firestore as db

app = FastAPI(title="warden-deadman")
STALE_MIN = 15


@app.get("/healthz")
def healthz():
    return {"ok": True, "role": "deadman"}


@app.post("/check")
def check():
    d = db.client().collection("health").document("watcher").get()
    last = d.to_dict().get("last_ok_at") if d.exists else None
    t = datetime.now(timezone.utc)
    stale = last is None or (t - datetime.fromisoformat(last)) > timedelta(minutes=STALE_MIN)
    db.client().collection("health").document("deadman").set({"ok": True, "last_ok_at": t.isoformat(), "watcher_last_ok": last, "watcher_stale": stale}, merge=True)
    if not stale:
        return {"stale": False, "watcher_last_ok": last}
    acted = []
    for inst in compute().list_instances():
        if inst.managed and inst.status == InstanceStatus.RUNNING and not inst.boot_disk_auto_delete:
            r = compute().stop(inst.ref); acted.append({"ref": inst.ref, "ok": r.ok, "observed": r.observed, "error": r.error})
    db.client().collection("notifications").document(f"deadman:{t.strftime('%Y%m%dT%H%M%S')}").set(
        {"text": f"🛑 DEADMAN: watcher tidak berdenyut sejak {last} → STOP {len(acted)} mesin", "ts": t.isoformat(), "acted": acted})
    return {"stale": True, "watcher_last_ok": last, "acted": acted}
