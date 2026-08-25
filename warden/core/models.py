"""Model domain Warden (Pydantic). Sumber kebenaran tunggal untuk bentuk dokumen Firestore."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from ulid import ULID


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


class InstanceStatus(StrEnum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    STOPPING = "STOPPING"
    STARTING = "STARTING"
    TERMINATED = "TERMINATED"
    UNKNOWN = "UNKNOWN"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    FINISHED_UNVERIFIED = "FINISHED_UNVERIFIED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class IncidentState(StrEnum):
    DETECTED = "DETECTED"
    SUPPRESSED = "SUPPRESSED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    TRIAGED = "TRIAGED"
    DIAGNOSING = "DIAGNOSING"
    DIAGNOSED = "DIAGNOSED"
    DECIDED = "DECIDED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    HELD = "HELD"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    FAILED_ACTION = "FAILED_ACTION"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


class Autonomy(StrEnum):
    L0 = "L0"   # amati
    L1 = "L1"   # usulkan (minta izin)
    L2 = "L2"   # lakukan lalu lapor
    L3 = "L3"   # lakukan diam (digest)


class Verdict(StrEnum):
    AUTO = "AUTO"
    NEED_APPROVAL = "NEED_APPROVAL"
    DENY = "DENY"
    HELD = "HELD"


class DecisionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    DONE = "DONE"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class BlastRadius(StrEnum):
    NONE = "none"
    THIS_RUN = "this_run"
    THIS_JOB = "this_job"
    BUDGET = "budget"
    ARTIFACTS = "artifacts"


class Action(StrEnum):
    NOTIFY = "notify"
    START_INSTANCE = "start_instance"
    RESUME_JOB = "resume_job"
    STOP_INSTANCE = "stop_instance"
    QUARANTINE_ARTIFACT = "quarantine_artifact"
    ROLLBACK_LAST_GOOD = "rollback_last_good"
    RELOCATE_ZONE = "relocate_zone"
    RESIZE_DISK = "resize_disk"
    KILL_PROCESS = "kill_process"
    CHANGE_MACHINE_TYPE = "change_machine_type"
    # delete_* SENGAJA TIDAK ADA (P8)


class Instance(BaseModel):
    ref: str                                   # "<zone>/<name>"
    name: str
    zone: str
    status: InstanceStatus = InstanceStatus.UNKNOWN
    machine_type: str = ""
    spot: bool = False
    labels: dict[str, str] = Field(default_factory=dict)
    boot_disk_auto_delete: bool | None = None
    termination_action: str = ""               # STOP | DELETE
    boot_id: str = ""
    last_stop_at: datetime | None = None
    hourly_price_usd: float = 0.0
    managed: bool = False
    job_id: str = ""
    last_seen: datetime = Field(default_factory=now)
    operator_active_until: datetime | None = None


class Job(BaseModel):
    job_id: str
    name: str = ""
    instance_ref: str = ""
    command: str = ""
    phase: str = ""
    status: JobStatus = JobStatus.PENDING
    run_id: str = ""
    last_step: int = 0
    last_heartbeat_at: datetime | None = None
    artifact_prefix: str = ""
    expect: dict[str, Any] = Field(default_factory=dict)   # ckpt_size_bytes, tol, jsonl_min_rows, files
    resume_cmd: str = ""
    operator_hold_until: datetime | None = None
    last_good_ckpt: dict[str, Any] = Field(default_factory=dict)  # path, sha256, step
    budget_cap_usd: float = 0.0
    spent_usd: float = 0.0
    legacy: bool = False                       # hanya stdout, tanpa warden.beat()
    autonomy_overrides: dict[str, str] = Field(default_factory=dict)


class Heartbeat(BaseModel):
    job_id: str
    run_id: str = ""
    ts: datetime = Field(default_factory=now)
    boot_id: str = ""
    phase: str = ""
    step: int | None = None
    epoch: int | None = None
    loss: float | None = None
    lr: float | None = None
    grad_norm: float | None = None
    step_per_s: float | None = None
    vram_used_mb: float | None = None
    vram_total_mb: float | None = None
    gpu_util: float | None = None
    cpu_pct: float | None = None
    disk_avail_gb: float | None = None
    log_mtime: datetime | None = None
    procs: list[dict[str, Any]] = Field(default_factory=list)   # {pid, ppid, cmd}
    open_writers: list[str] = Field(default_factory=list)
    operator_active: bool = False
    preempt_notice: bool = False
    synthetic: bool = False                    # dibangun parser log (mode legacy)


class Marker(BaseModel):
    job_id: str
    run_id: str = ""
    kind: str                                   # RUN_FIN | PHASE_START | PHASE_END | SMOKE_FIN | PREFLIGHT_FAIL | DONE_LEGACY
    ts: datetime = Field(default_factory=now)
    exit_code: int | None = None
    signal: str | None = None
    phase: str = ""
    boot_id: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)   # {path, bytes, sha256}
    evidence: dict[str, Any] = Field(default_factory=dict)          # rows, metrics
    signature: str = ""
    valid: bool = False
    invalid_reason: str = ""


class Incident(BaseModel):
    incident_id: str = Field(default_factory=lambda: new_id("inc"))
    job_id: str = ""
    instance_ref: str = ""
    dedupe_key: str = ""
    rule: str = ""
    severity: str = "warning"                  # info | warning | critical
    state: IncidentState = IncidentState.DETECTED
    summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    diagnosis: dict[str, Any] = Field(default_factory=dict)
    crosscheck: dict[str, Any] = Field(default_factory=dict)
    decision_ids: list[str] = Field(default_factory=list)
    llm_cost_usd: float = 0.0
    cost_burning_usd_per_hour: float = 0.0
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class Decision(BaseModel):
    decision_id: str = Field(default_factory=lambda: new_id("dec"))
    incident_id: str = ""
    job_id: str = ""
    action: Action
    params: dict[str, Any] = Field(default_factory=dict)
    autonomy: Autonomy = Autonomy.L1
    verdict: Verdict = Verdict.NEED_APPROVAL
    explain: list[str] = Field(default_factory=list)        # aturan mana lolos/gagal
    blast_radius: BlastRadius = BlastRadius.THIS_RUN
    cost_usd: float = 0.0
    status: DecisionStatus = DecisionStatus.PENDING
    approved_by: str = ""
    channel_msg_ref: str = ""
    expires_at: datetime | None = None
    dry_run_plan: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)    # requested, observed, diff
    created_at: datetime = Field(default_factory=now)


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: new_id("ev"))
    incident_id: str = ""
    kind: str                                   # log_window | marker | heartbeat | artifact_check | image | quota | status
    summary: str = ""
    uri: str = ""
    sha256: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now)


class AuditEntry(BaseModel):
    audit_id: str = Field(default_factory=lambda: new_id("aud"))
    actor: str                                  # warden | human:<id> | harness | deadman
    phase: str                                  # intent | result
    action: str
    target: str = ""
    decision_id: str = ""
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    ok: bool | None = None
    error: str = ""
    ts: datetime = Field(default_factory=now)
