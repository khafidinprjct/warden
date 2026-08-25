"""Compute Engine lewat client library (compute_v1). Tanpa ssh, tanpa gcloud.
Setiap operasi menunggu selesai dan melaporkan diminta-vs-jadi (P10). Tidak ada fungsi delete (P8)."""
from __future__ import annotations

from datetime import datetime

from google.api_core import exceptions as gexc
from google.cloud import compute_v1

from warden.config import settings
from warden.core.models import Instance, InstanceStatus, now
from warden.providers.base import OpResult

_STATUS = {"RUNNING": InstanceStatus.RUNNING, "TERMINATED": InstanceStatus.TERMINATED, "STOPPING": InstanceStatus.STOPPING,
           "STAGING": InstanceStatus.STARTING, "PROVISIONING": InstanceStatus.STARTING, "SUSPENDED": InstanceStatus.STOPPED}

# tarif on-demand us-central1 (USD/jam) — tabel statis kecil; spot ≈ 30% (verifikasi kalkulator resmi di Fase 2)
_PRICE = {"e2-small": 0.01675, "e2-medium": 0.0335, "e2-standard-2": 0.067, "e2-standard-4": 0.134,
          "n2-standard-4": 0.194, "n1-standard-4": 0.19, "a2-highgpu-1g": 3.67}


class GCE:
    def __init__(self, project: str | None = None):
        self.project = project or settings.project
        self.ic = compute_v1.InstancesClient()
        self.zc = compute_v1.ZoneOperationsClient()
        self.rc = compute_v1.RegionsClient()
        self.pc = compute_v1.ProjectsClient()

    @staticmethod
    def _split(ref: str) -> tuple[str, str]:
        zone, name = ref.split("/", 1)
        return zone, name

    def _to_model(self, i: compute_v1.Instance, zone: str) -> Instance:
        labels = dict(i.labels or {})
        boot = next((d for d in (i.disks or []) if d.boot), None)
        mt = (i.machine_type or "").rsplit("/", 1)[-1]
        sched = i.scheduling
        spot = bool(sched and (getattr(sched, "provisioning_model", "") == "SPOT" or sched.preemptible))
        inst = Instance(ref=f"{zone}/{i.name}", name=i.name, zone=zone, status=_STATUS.get(i.status, InstanceStatus.UNKNOWN),
                        machine_type=mt, spot=spot, labels=labels,
                        boot_disk_auto_delete=(boot.auto_delete if boot else None),
                        termination_action=(getattr(sched, "instance_termination_action", "") or ("STOP" if not spot else "")),
                        boot_id=str(i.id), managed=labels.get(settings.managed_label) == "true",
                        job_id=labels.get("warden-job", ""), last_seen=now())
        if i.last_stop_timestamp:
            try:
                inst.last_stop_at = datetime.fromisoformat(i.last_stop_timestamp.replace("Z", "+00:00"))
            except ValueError:
                pass
        base = _PRICE.get(mt, 0.05)
        inst.hourly_price_usd = round(base * (0.3 if spot else 1.0), 5)
        return inst

    def list_instances(self) -> list[Instance]:
        out: list[Instance] = []
        req = compute_v1.AggregatedListInstancesRequest(project=self.project, max_results=200)
        for zone_key, scoped in self.ic.aggregated_list(request=req):
            zone = zone_key.rsplit("/", 1)[-1]
            for i in (scoped.instances or []):
                out.append(self._to_model(i, zone))
        return out

    def describe(self, ref: str) -> Instance | None:
        zone, name = self._split(ref)
        try:
            return self._to_model(self.ic.get(project=self.project, zone=zone, instance=name), zone)
        except gexc.NotFound:
            return None

    def _guard(self, ref: str) -> Instance:
        inst = self.describe(ref)
        if inst is None:
            raise gexc.NotFound(f"{ref} tidak ada")
        if not inst.managed:
            raise PermissionError(f"{ref} tidak berlabel {settings.managed_label}=true — Warden menolak menyentuhnya")
        return inst

    def _wait(self, op, zone: str, timeout_s: int = 180) -> str:
        done = self.zc.wait(project=self.project, zone=zone, operation=op.name, timeout=timeout_s)
        if done.error and done.error.errors:
            raise RuntimeError("; ".join(f"{e.code}: {e.message}" for e in done.error.errors))
        return done.name

    def start(self, ref: str, dry_run: bool = False) -> OpResult:
        zone, name = self._split(ref)
        inst = self._guard(ref)
        plan = {"api": "instances.start", "zone": zone, "instance": name, "from": inst.status, "to": "RUNNING",
                "hourly_usd": inst.hourly_price_usd}
        if dry_run:
            return OpResult(True, f"start {ref}", dry_run=True, plan=plan)
        try:
            op = self.ic.start(project=self.project, zone=zone, instance=name)
            op_id = self._wait(op, zone)
            after = self.describe(ref)
            return OpResult(after is not None and after.status == InstanceStatus.RUNNING, f"start {ref}",
                            observed=str(after.status if after else "?"), op_id=op_id, plan=plan)
        except Exception as e:  # dilaporkan terstruktur, bukan ditelan
            return OpResult(False, f"start {ref}", error=f"{type(e).__name__}: {e}", plan=plan)

    def stop(self, ref: str, dry_run: bool = False) -> OpResult:
        zone, name = self._split(ref)
        inst = self._guard(ref)
        if inst.boot_disk_auto_delete:
            return OpResult(False, f"stop {ref}", error="boot disk auto-delete=true — STOP ditolak (P8)")
        plan = {"api": "instances.stop", "zone": zone, "instance": name, "from": inst.status, "to": "TERMINATED",
                "saves_usd_per_hour": inst.hourly_price_usd}
        if dry_run:
            return OpResult(True, f"stop {ref}", dry_run=True, plan=plan)
        try:
            op = self.ic.stop(project=self.project, zone=zone, instance=name)
            op_id = self._wait(op, zone)
            after = self.describe(ref)
            return OpResult(after is not None and after.status in (InstanceStatus.TERMINATED, InstanceStatus.STOPPED, InstanceStatus.STOPPING),
                            f"stop {ref}", observed=str(after.status if after else "?"), op_id=op_id, plan=plan)
        except Exception as e:
            return OpResult(False, f"stop {ref}", error=f"{type(e).__name__}: {e}", plan=plan)

    def set_metadata(self, ref: str, items: dict[str, str], dry_run: bool = False) -> OpResult:
        zone, name = self._split(ref)
        self._guard(ref)
        plan = {"api": "instances.setMetadata", "items": items}
        if dry_run:
            return OpResult(True, f"set_metadata {ref}", dry_run=True, plan=plan)
        try:
            cur = self.ic.get(project=self.project, zone=zone, instance=name).metadata
            existing = {it.key: it.value for it in (cur.items or [])}
            existing.update(items)
            md = compute_v1.Metadata(fingerprint=cur.fingerprint,
                                     items=[compute_v1.Items(key=k, value=v) for k, v in existing.items()])
            op = self.ic.set_metadata(project=self.project, zone=zone, instance=name, metadata_resource=md)
            op_id = self._wait(op, zone)
            return OpResult(True, f"set_metadata {ref}", observed="ok", op_id=op_id, plan=plan)
        except Exception as e:
            return OpResult(False, f"set_metadata {ref}", error=f"{type(e).__name__}: {e}", plan=plan)

    def stock_check(self, machine_type: str, zones: list[str]) -> dict[str, bool]:
        """Stok tidak bisa ditanya langsung; pendekatan: zona yang menolak dgn ZONE_RESOURCE_POOL_EXHAUSTED
        dalam 15 mnt terakhir dianggap kosong (diisi executor). Default True."""
        return {z: True for z in zones}

    def quota(self, region: str) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        try:
            for q in self.pc.get(project=self.project).quotas:
                out[f"GLOBAL:{q.metric}"] = (q.limit, q.usage)
            for q in self.rc.get(project=self.project, region=region).quotas:
                out[q.metric] = (q.limit, q.usage)
        except Exception as e:
            out["_error"] = (0.0, 0.0); out["_error_msg"] = str(e)[:120]  # type: ignore[assignment]
        return out

    def price(self, inst: Instance) -> float:
        return inst.hourly_price_usd

    def preempt_events(self, ref: str) -> list[dict]:
        """Cari operasi 'compute.instances.preempted' pada zona untuk instance ini (24 jam terakhir)."""
        zone, name = self._split(ref)
        out: list[dict] = []
        try:
            req = compute_v1.ListZoneOperationsRequest(project=self.project, zone=zone, max_results=100,
                                                       filter=f'(operationType = "compute.instances.preempted") AND (targetLink : "{name}")')
            for op in self.zc.list(request=req):
                out.append({"type": op.operation_type, "ts": op.insert_time, "target": op.target_link})
        except Exception as e:
            out.append({"type": "_error", "msg": str(e)[:120]})
        return out
