"""Skema keluaran LLM (dipaksa lewat output_schema ADK). Bukti sebelum tindakan (urutan field disengaja)."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Category(StrEnum):
    oom_gpu = "oom_gpu"
    oom_host = "oom_host"
    nan_divergence = "nan_divergence"
    nan_input = "nan_input"
    plateau = "plateau"
    dependency_missing = "dependency_missing"
    env_broken = "env_broken"
    kernel_fallback = "kernel_fallback"
    disk_full = "disk_full"
    data_error = "data_error"
    network_transient = "network_transient"
    preempt = "preempt"
    config_error = "config_error"
    code_bug = "code_bug"
    unknown = "unknown"


class Transience(StrEnum):
    transient = "transient"
    permanent = "permanent"
    ambiguous = "ambiguous"


class Recommended(StrEnum):
    resume_same = "resume_same"                    # transient failure: run the same command again from the last VERIFIED checkpoint
    resume_smaller_batch = "resume_smaller_batch"  # GPU OOM: action_params {"batch_scale": 0.5}
    resume_fewer_workers = "resume_fewer_workers"  # host OOM: action_params {"workers_scale": 0.5}
    restart_clean = "restart_clean"                # corrupted local state: fresh run (old artifacts archived, not deleted)
    rollback_last_good = "rollback_last_good"      # divergence: resume from an older intact checkpoint, action_params {"lr_scale": 0.5, "back": 1}
    kill_and_resume = "kill_and_resume"            # hung / duplicate process
    clean_disk = "clean_disk"                      # disk full: remove local checkpoints that already live in Storage
    resize_disk = "resize_disk"                    # disk full and nothing to clean: action_params {"grow_pct": 50}
    relocate_zone = "relocate_zone"                # zone stock-out
    change_machine_type = "change_machine_type"    # needs more memory/CPU: action_params {"machine_type": "..."} or {"mode": "bigger"}
    stop = "stop"                                  # permanent failure: stop the machine to stop the spend
    escalate = "escalate"
    patch_suggest = "patch_suggest"                # permanent failure with a concrete code/config fix: machine is stopped, fix goes to the human
    noop = "noop"


class Diagnosis(BaseModel):
    category: Category
    confidence: float = Field(ge=0, le=1)
    transient_or_permanent: Transience
    evidence_lines: list[int] = Field(description="nomor baris di log_tail yang menjadi bukti (wajib kecuali unknown)")
    evidence_quotes: list[str] = Field(max_length=5, description="kutipan ≤200 karakter, harus substring baris yang ditunjuk")
    root_cause: str = Field(max_length=400)
    culprit_frame: str | None = Field(default=None, description="for OOM: the frame that ALLOCATES, not the one that fails")
    recommended_action: Recommended
    action_params: dict = Field(default_factory=dict)
    blast_radius: str = Field(description="none|this_run|this_job|budget|artifacts")
    needs_human: bool
    human_summary: str = Field(max_length=280, description="one-sentence English summary for the human card")
    falsifiable_check: str = Field(max_length=200, description="if the diagnosis is right, after action X the number Y changes")


PERMANENT = {Category.env_broken, Category.config_error, Category.code_bug, Category.dependency_missing, Category.nan_input, Category.data_error}
