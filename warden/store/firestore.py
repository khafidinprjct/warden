"""Akses Firestore: kebenaran Warden. Satu klien, repos tipis per koleksi, lease transaksional."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from google.cloud import firestore
from pydantic import BaseModel

from warden.config import settings
from warden.core.models import AuditEntry, Decision, Evidence, Heartbeat, Incident, Instance, Job, Marker, now

T = TypeVar("T", bound=BaseModel)

_client: firestore.Client | None = None


def client() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(project=settings.project, database=(settings.firestore_db if settings.firestore_db and settings.firestore_db != "(default)" else None))
    return _client


def _dump(m: BaseModel) -> dict[str, Any]:
    return m.model_dump(mode="json")


def _docid(x: str) -> str:
    """ID dokumen Firestore tidak boleh memuat '/'; ref mesin 'zone/name' → 'zone__name'."""
    return x.replace("/", "__")


class Repo:
    """CRUD generik per koleksi. ID dokumen = field kunci model."""

    def __init__(self, coll: str, model: type[T], key: str):
        self.coll, self.model, self.key = coll, model, key

    def put(self, m: T) -> T:
        client().collection(self.coll).document(_docid(getattr(m, self.key))).set(_dump(m))
        return m

    def get(self, id_: str) -> T | None:
        d = client().collection(self.coll).document(_docid(id_)).get()
        return self.model.model_validate(d.to_dict()) if d.exists else None

    def list(self, limit: int = 500, **eq) -> list[T]:
        q = client().collection(self.coll)
        for k, v in eq.items():
            q = q.where(filter=firestore.FieldFilter(k, "==", v))
        return [self.model.model_validate(d.to_dict()) for d in q.limit(limit).stream()]

    def delete(self, id_: str) -> None:
        client().collection(self.coll).document(_docid(id_)).delete()


fleet = Repo("fleet", Instance, "ref")
jobs = Repo("jobs", Job, "job_id")
incidents = Repo("incidents", Incident, "incident_id")
decisions = Repo("decisions", Decision, "decision_id")
evidence = Repo("evidence", Evidence, "evidence_id")


def put_heartbeat(hb: Heartbeat) -> None:
    doc = client().collection("runs").document(hb.job_id).collection("heartbeats").document(hb.ts.strftime("%Y%m%dT%H%M%S%f"))
    doc.set(_dump(hb))
    client().collection("runs").document(hb.job_id).set({"last": _dump(hb)}, merge=True)


def last_heartbeat(job_id: str) -> Heartbeat | None:
    d = client().collection("runs").document(job_id).get()
    if not d.exists or "last" not in d.to_dict():
        return None
    return Heartbeat.model_validate(d.to_dict()["last"])


def recent_heartbeats(job_id: str, n: int = 30) -> list[Heartbeat]:
    q = client().collection("runs").document(job_id).collection("heartbeats").order_by("ts", direction=firestore.Query.DESCENDING).limit(n)
    return [Heartbeat.model_validate(d.to_dict()) for d in q.stream()][::-1]


def put_marker(mk: Marker) -> None:
    client().collection("markers").document(f"{mk.job_id}:{mk.run_id}:{mk.kind}").set(_dump(mk))


def get_marker(job_id: str, run_id: str, kind: str) -> Marker | None:
    d = client().collection("markers").document(f"{job_id}:{run_id}:{kind}").get()
    return Marker.model_validate(d.to_dict()) if d.exists else None


def audit(e: AuditEntry) -> None:
    client().collection("audit").document(e.audit_id).set(_dump(e))


def health(source: str, ok: bool, error: str = "") -> None:
    ref = client().collection("health").document(source)
    snap = ref.get()
    prev = snap.to_dict() if snap.exists else {}
    fails = 0 if ok else int(prev.get("consecutive_failures", 0)) + 1
    ref.set({"ok": ok, "last_error": error, "consecutive_failures": fails,
             "last_ok_at": now().isoformat() if ok else prev.get("last_ok_at"),
             "updated_at": now().isoformat()}, merge=True)


def heartbeat_self(source: str, extra: dict[str, Any] | None = None) -> None:
    """Denyut Warden sendiri (P4): ditulis di jalur SUKSES."""
    client().collection("health").document(source).set({"ok": True, "last_ok_at": now().isoformat(),
                                                         "consecutive_failures": 0, **(extra or {})}, merge=True)


@firestore.transactional
def _acquire(tx, ref, holder: str, ttl_s: int) -> bool:
    snap = ref.get(transaction=tx)
    t = now()
    if snap.exists:
        d = snap.to_dict()
        exp = datetime.fromisoformat(d["expires_at"])
        if exp > t and d.get("holder") != holder:
            return False
    tx.set(ref, {"holder": holder, "expires_at": (t + timedelta(seconds=ttl_s)).isoformat()})
    return True


def acquire_lease(job_id: str, holder: str, ttl_s: int = 300) -> bool:
    """Kunci per job (anti balapan Warden vs Warden / operator). True = dapat."""
    ref = client().collection("leases").document(job_id)
    return _acquire(client().transaction(), ref, holder, ttl_s)


def release_lease(job_id: str, holder: str) -> None:
    ref = client().collection("leases").document(job_id)
    snap = ref.get()
    if snap.exists and snap.to_dict().get("holder") == holder:
        ref.delete()


def cost_add(day: str, field: str, usd: float, resource: str = "") -> None:
    ref = client().collection("costs").document(day)
    ref.set({field: firestore.Increment(usd), "updated_at": now().isoformat()}, merge=True)
    if resource:
        ref.collection("by_resource").document(resource.replace("/", "_")).set({field: firestore.Increment(usd)}, merge=True)


def cost_today() -> dict[str, Any]:
    d = client().collection("costs").document(now().strftime("%Y-%m-%d")).get()
    return d.to_dict() if d.exists else {}


def mailbox_post(job_id: str, cmd: str, args: dict[str, Any], decision_id: str = "", signer=None) -> dict[str, Any]:
    """One pending command per job (the harness polls GET /cmd/<job>). Signed so the agent can reject forged commands."""
    from warden.core.models import new_id
    doc = {"cmd": cmd, "args": args, "decision_id": decision_id, "ts": now().isoformat(), "nonce": new_id("cmd")}
    doc["sig"] = signer(doc) if signer else ""
    client().collection("cmd").document(job_id).set(doc)
    return doc


def cmd_result_put(job_id: str, res: dict[str, Any]) -> None:
    client().collection("cmd_results").document(f"{job_id}:{res.get('nonce', 'x')}").set({**res, "received_at": now().isoformat()})


def cmd_result_get(job_id: str, nonce: str) -> dict[str, Any] | None:
    d = client().collection("cmd_results").document(f"{job_id}:{nonce}").get()
    return d.to_dict() if d.exists else None


def stockout_mark(zone: str, machine_type: str, error: str = "") -> None:
    client().collection("stockouts").document(f"{zone}:{machine_type}").set({"zone": zone, "machine_type": machine_type, "error": error[:200], "ts": now().isoformat()})


def stockout_recent(zone: str, machine_type: str, minutes: int = 30) -> bool:
    d = client().collection("stockouts").document(f"{zone}:{machine_type}").get()
    if not d.exists:
        return False
    return (now() - datetime.fromisoformat(d.to_dict()["ts"])) < timedelta(minutes=minutes)
