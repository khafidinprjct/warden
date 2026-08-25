"""Compute palsu in-memory: meniru API GCE untuk tes & latihan tanpa biaya. Bisa disuntik skenario."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from warden.core.models import Instance, InstanceStatus, now
from warden.providers.base import OpResult


class FakeGCE:
    def __init__(self):
        self.instances: dict[str, Instance] = {}
        self.stock: dict[str, bool] = {}
        self.quotas: dict[str, tuple[float, float]] = {"CPUS": (24, 4), "SSD_TOTAL_GB": (500, 100)}
        self.events: dict[str, list[dict]] = {}
        self.calls: list[tuple] = []           # jejak panggilan untuk asersi tes
        self.fail_next: dict[str, str] = {}    # ref -> pesan error yang akan dikembalikan sekali
        # persistensi opsional (WARDEN_FAKE_STATE=path): dua proses (core + uji) melihat armada palsu yang sama
        self._path = Path(os.environ["WARDEN_FAKE_STATE"]) if os.environ.get("WARDEN_FAKE_STATE") else None
        self._mtime = 0.0
        self._load()

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        m = self._path.stat().st_mtime
        if m <= self._mtime:
            return
        self._mtime = m
        try:
            data = json.loads(self._path.read_text())
            self.instances = {k: Instance.model_validate(v) for k, v in data.get("instances", {}).items()}
        except (ValueError, OSError):
            pass

    def _save(self) -> None:
        if not self._path:
            return
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"instances": {k: v.model_dump(mode="json") for k, v in self.instances.items()}}))
        os.replace(tmp, self._path); self._mtime = self._path.stat().st_mtime

    # --- pengaturan skenario ---
    def add(self, name: str, zone: str = "us-central1-a", **kw) -> Instance:
        inst = Instance(ref=f"{zone}/{name}", name=name, zone=zone, status=InstanceStatus.RUNNING,
                        machine_type=kw.pop("machine_type", "e2-medium"), spot=kw.pop("spot", True),
                        labels=kw.pop("labels", {"warden-managed": "true"}),
                        boot_disk_auto_delete=kw.pop("boot_disk_auto_delete", False),
                        termination_action=kw.pop("termination_action", "STOP"),
                        boot_id=kw.pop("boot_id", "boot-1"), hourly_price_usd=kw.pop("hourly_price_usd", 0.0335), **kw)
        inst.managed = inst.labels.get("warden-managed") == "true"
        self.instances[inst.ref] = inst
        self._save()
        return inst

    def preempt(self, ref: str) -> None:
        i = self.instances[ref]
        i.status = InstanceStatus.TERMINATED
        i.last_stop_at = now()
        self.events.setdefault(ref, []).append({"type": "compute.instances.preempted", "ts": now().isoformat()})
        self._save()

    # --- antarmuka Compute ---
    def list_instances(self) -> list[Instance]:
        self._load()
        return list(self.instances.values())

    def describe(self, ref: str) -> Instance | None:
        self._load()
        return self.instances.get(ref)

    def _op(self, kind: str, ref: str, dry_run: bool, target: InstanceStatus) -> OpResult:
        self.calls.append((kind, ref, dry_run))
        self._load()
        inst = self.instances.get(ref)
        if inst is None:
            return OpResult(False, f"{kind} {ref}", error="instance tidak ada")
        plan = {"api": f"instances.{kind}", "zone": inst.zone, "instance": inst.name, "from": inst.status, "to": target}
        if dry_run:
            return OpResult(True, f"{kind} {ref}", dry_run=True, plan=plan)
        if ref in self.fail_next:
            return OpResult(False, f"{kind} {ref}", error=self.fail_next.pop(ref), plan=plan)
        inst.status = target
        if kind == "start":
            inst.boot_id = f"boot-{len(self.calls)}"
        self._save()
        return OpResult(True, f"{kind} {ref}", observed=str(inst.status), op_id=f"op-{len(self.calls)}", plan=plan)

    def start(self, ref: str, dry_run: bool = False) -> OpResult:
        return self._op("start", ref, dry_run, InstanceStatus.RUNNING)

    def stop(self, ref: str, dry_run: bool = False) -> OpResult:
        return self._op("stop", ref, dry_run, InstanceStatus.STOPPED)

    def set_metadata(self, ref: str, items: dict[str, str], dry_run: bool = False) -> OpResult:
        self.calls.append(("set_metadata", ref, dry_run, items))
        return OpResult(True, f"set_metadata {ref}", observed="ok", dry_run=dry_run, plan={"items": items})

    def stock_check(self, machine_type: str, zones: list[str]) -> dict[str, bool]:
        return {z: self.stock.get(z, True) for z in zones}

    def quota(self, region: str) -> dict[str, tuple[float, float]]:
        return dict(self.quotas)

    def price(self, inst: Instance) -> float:
        return inst.hourly_price_usd

    def preempt_events(self, ref: str) -> list[dict]:
        return list(self.events.get(ref, []))

    # --- recovery / lifecycle operations (same contract as GCE) ---
    def price_of(self, machine_type: str, spot: bool) -> float:
        base = {"e2-small": 0.01675, "e2-medium": 0.0335, "e2-standard-2": 0.067, "e2-standard-4": 0.134, "e2-standard-8": 0.268}.get(machine_type, 0.05)
        return round(base * (0.3 if spot else 1.0), 5)

    def set_machine_type(self, ref: str, machine_type: str, dry_run: bool = False) -> OpResult:
        self.calls.append(("set_machine_type", ref, dry_run, machine_type)); self._load()
        inst = self.instances.get(ref)
        if inst is None:
            return OpResult(False, f"set_machine_type {ref}", error="instance tidak ada")
        plan = {"api": "instances.setMachineType", "instance": inst.name, "from": inst.machine_type, "to": machine_type,
                "hourly_usd_from": inst.hourly_price_usd, "hourly_usd_to": self.price_of(machine_type, inst.spot)}
        if dry_run:
            return OpResult(True, f"set_machine_type {ref}", dry_run=True, plan=plan)
        if inst.status == InstanceStatus.RUNNING:
            return OpResult(False, f"set_machine_type {ref}", error="instance must be TERMINATED/STOPPED", plan=plan)
        inst.machine_type = machine_type; inst.hourly_price_usd = plan["hourly_usd_to"]; self._save()
        return OpResult(True, f"set_machine_type {ref}", observed=machine_type, op_id=f"op-{len(self.calls)}", plan=plan)

    def resize_disk(self, ref: str, size_gb: int, dry_run: bool = False) -> OpResult:
        self.calls.append(("resize_disk", ref, dry_run, size_gb)); self._load()
        inst = self.instances.get(ref)
        if inst is None:
            return OpResult(False, f"resize_disk {ref}", error="instance tidak ada")
        cur = int(inst.labels.get("_disk_gb", 20))
        plan = {"api": "disks.resize", "instance": inst.name, "from_gb": cur, "to_gb": size_gb, "extra_usd_per_month": round((size_gb - cur) * 0.1, 2)}
        if dry_run:
            return OpResult(True, f"resize_disk {ref}", dry_run=True, plan=plan)
        if size_gb <= cur:
            return OpResult(False, f"resize_disk {ref}", error="disks can only grow", plan=plan)
        inst.labels["_disk_gb"] = str(size_gb); self._save()
        return OpResult(True, f"resize_disk {ref}", observed=f"{size_gb} GB", op_id=f"op-{len(self.calls)}", plan=plan)

    def create(self, spec: dict, dry_run: bool = False) -> OpResult:
        self.calls.append(("create", spec.get("zone"), dry_run, spec.get("name")))
        zone, name = spec["zone"], spec["name"]
        plan = {"api": "instances.insert", "zone": zone, "instance": name, "machine_type": spec.get("machine_type", "e2-medium"),
                "spot": spec.get("spot", True), "hourly_usd": self.price_of(spec.get("machine_type", "e2-medium"), spec.get("spot", True))}
        if dry_run:
            return OpResult(True, f"create {zone}/{name}", dry_run=True, plan=plan)
        if not self.stock.get(zone, True):
            return OpResult(False, f"create {zone}/{name}", error="ZONE_RESOURCE_POOL_EXHAUSTED: The zone does not have enough resources", plan=plan)
        inst = self.add(name, zone, machine_type=spec.get("machine_type", "e2-medium"), spot=spec.get("spot", True),
                        labels={"warden-managed": "true", "warden-job": spec.get("job_id", ""), **spec.get("labels", {})},
                        hourly_price_usd=plan["hourly_usd"])
        inst.job_id = spec.get("job_id", ""); inst.boot_id = f"boot-{len(self.calls)}"; self._save()
        return OpResult(True, f"create {zone}/{name}", observed=inst.ref, op_id=f"op-{len(self.calls)}", plan=plan)

    def relocate(self, ref: str, target_zone: str, dry_run: bool = False, spot: bool | None = None) -> OpResult:
        self.calls.append(("relocate", ref, dry_run, target_zone)); self._load()
        inst = self.instances.get(ref)
        if inst is None:
            return OpResult(False, f"relocate {ref}", error="instance tidak ada")
        new_name = f"{inst.name}-{target_zone.rsplit('-', 1)[-1]}"
        plan = {"api": "disks.createSnapshot + disks.insert + instances.insert", "from": ref, "to": f"{target_zone}/{new_name}",
                "machine_type": inst.machine_type, "old_instance": "kept STOPPED (never deleted)"}
        if dry_run:
            return OpResult(True, f"relocate {ref}", dry_run=True, plan=plan)
        if inst.status == InstanceStatus.RUNNING:
            return OpResult(False, f"relocate {ref}", error="instance must be stopped before relocation", plan=plan)
        r = self.create({"zone": target_zone, "name": new_name, "machine_type": inst.machine_type, "spot": (inst.spot if spot is None else bool(spot)), "job_id": inst.job_id,
                         "labels": {"warden-relocated-from": inst.name}}, dry_run=False)
        if not r.ok:
            return OpResult(False, f"relocate {ref}", error=r.error, plan=plan)
        inst.labels["warden-relocated-to"] = new_name; self._save()
        return OpResult(True, f"relocate {ref}", observed=r.observed, op_id=r.op_id, plan=plan)
