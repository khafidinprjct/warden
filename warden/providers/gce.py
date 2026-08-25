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
_PRICE = {"e2-small": 0.01675, "e2-medium": 0.0335, "e2-standard-2": 0.067, "e2-standard-4": 0.134, "e2-standard-8": 0.268,
          "n2-standard-4": 0.194, "n2-standard-8": 0.388, "n1-standard-4": 0.19, "a2-highgpu-1g": 3.67, "g2-standard-4": 0.71}
# one step up the same family: the "bigger machine" rung of the OOM ladder
_BIGGER = {"e2-small": "e2-medium", "e2-medium": "e2-standard-2", "e2-standard-2": "e2-standard-4", "e2-standard-4": "e2-standard-8",
           "n2-standard-4": "n2-standard-8", "n1-standard-4": "n1-standard-8"}


def bigger_machine(mt: str) -> str:
    return _BIGGER.get(mt, "")


class GCE:
    def __init__(self, project: str | None = None):
        self.project = project or settings.project
        self.ic = compute_v1.InstancesClient()
        self.zc = compute_v1.ZoneOperationsClient()
        self.rc = compute_v1.RegionsClient()
        self.pc = compute_v1.ProjectsClient()
        self.dc = compute_v1.DisksClient()
        self.goc = compute_v1.GlobalOperationsClient()

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

    def price_of(self, machine_type: str, spot: bool) -> float:
        return round(_PRICE.get(machine_type, 0.05) * (0.3 if spot else 1.0), 5)

    def _wait_global(self, op, timeout_s: int = 600) -> str:
        done = self.goc.wait(project=self.project, operation=op.name, timeout=timeout_s)
        if done.error and done.error.errors:
            raise RuntimeError("; ".join(f"{e.code}: {e.message}" for e in done.error.errors))
        return done.name

    def _boot_disk(self, zone: str, name: str):
        i = self.ic.get(project=self.project, zone=zone, instance=name)
        boot = next((d for d in (i.disks or []) if d.boot), None)
        if boot is None:
            raise RuntimeError("no boot disk")
        dname = boot.source.rsplit("/", 1)[-1]
        return i, self.dc.get(project=self.project, zone=zone, disk=dname)

    def set_machine_type(self, ref: str, machine_type: str, dry_run: bool = False) -> OpResult:
        """Requires TERMINATED. Changes the type in place (same disk, same job state); caller starts it afterwards."""
        zone, name = self._split(ref)
        inst = self._guard(ref)
        plan = {"api": "instances.setMachineType", "zone": zone, "instance": name, "from": inst.machine_type, "to": machine_type,
                "hourly_usd_from": inst.hourly_price_usd, "hourly_usd_to": self.price_of(machine_type, inst.spot)}
        if dry_run:
            return OpResult(True, f"set_machine_type {ref}", dry_run=True, plan=plan)
        if inst.status == InstanceStatus.RUNNING:
            return OpResult(False, f"set_machine_type {ref}", error="instance must be TERMINATED first", plan=plan)
        try:
            req = compute_v1.InstancesSetMachineTypeRequest(machine_type=f"zones/{zone}/machineTypes/{machine_type}")
            op = self.ic.set_machine_type(project=self.project, zone=zone, instance=name, instances_set_machine_type_request_resource=req)
            op_id = self._wait(op, zone)
            after = self.describe(ref)
            return OpResult(after is not None and after.machine_type == machine_type, f"set_machine_type {ref}",
                            observed=after.machine_type if after else "?", op_id=op_id, plan=plan)
        except Exception as e:
            return OpResult(False, f"set_machine_type {ref}", error=f"{type(e).__name__}: {e}", plan=plan)

    def resize_disk(self, ref: str, size_gb: int, dry_run: bool = False) -> OpResult:
        """Grow the boot disk (disks can only grow). The harness grows the filesystem on the mailbox command grow_fs."""
        zone, name = self._split(ref)
        self._guard(ref)
        try:
            _, disk = self._boot_disk(zone, name)
        except Exception as e:
            return OpResult(False, f"resize_disk {ref}", error=f"{type(e).__name__}: {e}")
        plan = {"api": "disks.resize", "zone": zone, "disk": disk.name, "from_gb": int(disk.size_gb), "to_gb": int(size_gb),
                "extra_usd_per_month": round(max(0, int(size_gb) - int(disk.size_gb)) * 0.10, 2)}
        if dry_run:
            return OpResult(True, f"resize_disk {ref}", dry_run=True, plan=plan)
        if int(size_gb) <= int(disk.size_gb):
            return OpResult(False, f"resize_disk {ref}", error="disks can only grow", plan=plan)
        try:
            op = self.dc.resize(project=self.project, zone=zone, disk=disk.name, disks_resize_request_resource=compute_v1.DisksResizeRequest(size_gb=int(size_gb)))
            op_id = self._wait(op, zone)
            after = self.dc.get(project=self.project, zone=zone, disk=disk.name)
            return OpResult(int(after.size_gb) == int(size_gb), f"resize_disk {ref}", observed=f"{after.size_gb} GB", op_id=op_id, plan=plan)
        except Exception as e:
            return OpResult(False, f"resize_disk {ref}", error=f"{type(e).__name__}: {e}", plan=plan)

    def create(self, spec: dict, dry_run: bool = False) -> OpResult:
        """Create a Warden-managed VM from a spec: {name, zone, machine_type, spot, image_family, image_project, disk_gb, disk_type,
        labels, metadata, service_account, source_disk (relocation), job_id}. Always STOP on preemption, never auto-delete the disk (P8)."""
        zone, name = spec["zone"], spec["name"]
        mt = spec.get("machine_type", "e2-medium"); spot = bool(spec.get("spot", True))
        plan = {"api": "instances.insert", "zone": zone, "instance": name, "machine_type": mt, "spot": spot,
                "hourly_usd": self.price_of(mt, spot), "termination_action": "STOP", "boot_disk_auto_delete": False}
        if dry_run:
            return OpResult(True, f"create {zone}/{name}", dry_run=True, plan=plan)
        labels = {settings.managed_label: "true", "warden-job": spec.get("job_id", ""), **{k: v for k, v in (spec.get("labels") or {}).items()}}
        if spec.get("source_disk"):
            disk = compute_v1.AttachedDisk(boot=True, auto_delete=False, source=spec["source_disk"])
        else:
            disk = compute_v1.AttachedDisk(boot=True, auto_delete=False, initialize_params=compute_v1.AttachedDiskInitializeParams(
                source_image=f"projects/{spec.get('image_project', 'ubuntu-os-cloud')}/global/images/family/{spec.get('image_family', 'ubuntu-2404-lts-amd64')}",
                disk_size_gb=int(spec.get("disk_gb", 20)), disk_type=f"zones/{zone}/diskTypes/{spec.get('disk_type', 'pd-balanced')}"))
        sched = compute_v1.Scheduling(provisioning_model="SPOT" if spot else "STANDARD", automatic_restart=not spot,
                                      on_host_maintenance="TERMINATE" if spot or spec.get("gpu") else "MIGRATE",
                                      **({"instance_termination_action": "STOP"} if spot else {}))
        md = compute_v1.Metadata(items=[compute_v1.Items(key=k, value=str(v)) for k, v in (spec.get("metadata") or {}).items()])
        sa = compute_v1.ServiceAccount(email=spec.get("service_account") or f"warden-vm@{self.project}.iam.gserviceaccount.com",
                                       scopes=["https://www.googleapis.com/auth/cloud-platform"])
        nic = compute_v1.NetworkInterface(network="global/networks/default", access_configs=[compute_v1.AccessConfig(name="External NAT", type_="ONE_TO_ONE_NAT")])
        body = compute_v1.Instance(name=name, machine_type=f"zones/{zone}/machineTypes/{mt}", disks=[disk], scheduling=sched,
                                   labels=labels, metadata=md, service_accounts=[sa], network_interfaces=[nic])
        if spec.get("gpu"):
            body.guest_accelerators = [compute_v1.AcceleratorConfig(accelerator_type=f"zones/{zone}/acceleratorTypes/{spec['gpu']}", accelerator_count=int(spec.get("gpu_count", 1)))]
        try:
            op = self.ic.insert(project=self.project, zone=zone, instance_resource=body)
            op_id = self._wait(op, zone, timeout_s=300)
            after = self.describe(f"{zone}/{name}")
            return OpResult(after is not None, f"create {zone}/{name}", observed=(f"{zone}/{name}" if after else "?"), op_id=op_id, plan=plan)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            return OpResult(False, f"create {zone}/{name}", error=msg, plan=plan)

    def relocate(self, ref: str, target_zone: str, dry_run: bool = False, spot: bool | None = None) -> OpResult:
        """Move a stopped VM to another zone WITHOUT losing job state: snapshot boot disk → disk in target zone → new instance with the
        same machine type, labels, metadata and service account. The old instance stays STOPPED (never deleted, P8)."""
        zone, name = self._split(ref)
        inst = self._guard(ref)
        stamp = now().strftime("%Y%m%d%H%M%S")
        new_name = f"{name}-{target_zone.rsplit('-', 1)[-1]}{stamp[-4:]}"
        use_spot = inst.spot if spot is None else bool(spot)
        plan = {"api": "disks.createSnapshot + disks.insert + instances.insert", "from": ref, "to": f"{target_zone}/{new_name}",
                "machine_type": inst.machine_type, "spot": use_spot, "hourly_usd": self.price_of(inst.machine_type, use_spot), "old_instance": "kept STOPPED (never deleted)",
                "snapshot_usd_per_month_est": 0.5}
        if dry_run:
            return OpResult(True, f"relocate {ref}", dry_run=True, plan=plan)
        if inst.status == InstanceStatus.RUNNING:
            return OpResult(False, f"relocate {ref}", error="instance must be stopped before relocation", plan=plan)
        try:
            src, disk = self._boot_disk(zone, name)
            snap = f"{name}-reloc-{stamp}"
            op = self.dc.create_snapshot(project=self.project, zone=zone, disk=disk.name, snapshot_resource=compute_v1.Snapshot(name=snap))
            self._wait(op, zone, timeout_s=900)
            op = self.dc.insert(project=self.project, zone=target_zone, disk_resource=compute_v1.Disk(
                name=new_name, source_snapshot=f"global/snapshots/{snap}", type_=f"zones/{target_zone}/diskTypes/{disk.type_.rsplit('/', 1)[-1]}", size_gb=disk.size_gb))
            self._wait(op, target_zone, timeout_s=600)
            md = {it.key: it.value for it in (src.metadata.items or [])}
            spec = {"name": new_name, "zone": target_zone, "machine_type": inst.machine_type, "spot": use_spot, "job_id": inst.job_id,
                    "labels": {**{k: v for k, v in inst.labels.items() if not k.startswith("warden-relocated")}, "warden-relocated-from": name},
                    "metadata": md, "service_account": (src.service_accounts[0].email if src.service_accounts else ""),
                    "source_disk": f"zones/{target_zone}/disks/{new_name}"}
            r = self.create(spec)
            if not r.ok:
                return OpResult(False, f"relocate {ref}", error=r.error, plan=plan)
            # mark the old one (labels are the only mutation on it)
            try:
                cur = self.ic.get(project=self.project, zone=zone, instance=name)
                lab = dict(cur.labels or {}); lab["warden-relocated-to"] = new_name
                op = self.ic.set_labels(project=self.project, zone=zone, instance=name,
                                        instances_set_labels_request_resource=compute_v1.InstancesSetLabelsRequest(labels=lab, label_fingerprint=cur.label_fingerprint))
                self._wait(op, zone)
            except Exception:
                pass
            return OpResult(True, f"relocate {ref}", observed=r.observed, op_id=r.op_id, plan=plan)
        except Exception as e:
            return OpResult(False, f"relocate {ref}", error=f"{type(e).__name__}: {e}", plan=plan)

    def preempt_events(self, ref: str) -> list[dict]:
        """Cari operasi 'compute.instances.preempted' pada zona untuk instance ini (24 jam terakhir)."""
        zone, name = self._split(ref)
        out: list[dict] = []
        try:
            # filter on operationType only; the target is matched here (the API's substring filter on targetLink returned nothing
            # for a real preemption on 26 Aug → the incident was labelled stopped_external and the relocate rung never applied)
            req = compute_v1.ListZoneOperationsRequest(project=self.project, zone=zone, max_results=200,
                                                       filter='operationType = "compute.instances.preempted"')
            for op in self.zc.list(request=req):
                if (op.target_link or "").endswith(f"/instances/{name}"):
                    out.append({"type": op.operation_type, "ts": op.insert_time, "target": op.target_link})
        except Exception as e:
            out.append({"type": "_error", "msg": str(e)[:120]})
        return out
