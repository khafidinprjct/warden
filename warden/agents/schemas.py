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
    resume_same = "resume_same"
    resume_smaller_batch = "resume_smaller_batch"
    restart_clean = "restart_clean"
    stop = "stop"
    escalate = "escalate"
    patch_suggest = "patch_suggest"
    noop = "noop"


class Diagnosis(BaseModel):
    category: Category
    confidence: float = Field(ge=0, le=1)
    transient_or_permanent: Transience
    evidence_lines: list[int] = Field(description="nomor baris di log_tail yang menjadi bukti (wajib kecuali unknown)")
    evidence_quotes: list[str] = Field(max_length=5, description="kutipan ≤200 karakter, harus substring baris yang ditunjuk")
    root_cause: str = Field(max_length=400)
    culprit_frame: str | None = Field(default=None, description="untuk OOM: frame yang MENGALOKASIKAN, bukan yang gagal")
    recommended_action: Recommended
    action_params: dict = Field(default_factory=dict)
    blast_radius: str = Field(description="none|this_run|this_job|budget|artifacts")
    needs_human: bool
    human_summary_id: str = Field(max_length=280, description="ringkasan Bahasa Indonesia untuk kartu Discord")
    falsifiable_check: str = Field(max_length=200, description="kalau diagnosis benar, setelah tindakan X angka Y berubah")


PERMANENT = {Category.env_broken, Category.config_error, Category.code_bug, Category.dependency_missing, Category.nan_input, Category.data_error}
