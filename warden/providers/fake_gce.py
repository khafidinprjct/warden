"""Compute palsu in-memory: meniru API GCE untuk tes & latihan tanpa biaya. Bisa disuntik skenario."""
from __future__ import annotations

from datetime import datetime

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
        return inst

    def preempt(self, ref: str) -> None:
        i = self.instances[ref]
        i.status = InstanceStatus.TERMINATED
        i.last_stop_at = now()
        self.events.setdefault(ref, []).append({"type": "compute.instances.preempted", "ts": now().isoformat()})

    # --- antarmuka Compute ---
    def list_instances(self) -> list[Instance]:
        return list(self.instances.values())

    def describe(self, ref: str) -> Instance | None:
        return self.instances.get(ref)

    def _op(self, kind: str, ref: str, dry_run: bool, target: InstanceStatus) -> OpResult:
        self.calls.append((kind, ref, dry_run))
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
