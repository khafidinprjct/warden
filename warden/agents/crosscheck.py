"""Cek silang deterministik atas klaim LLM (P1). Klaim yang tak lolos → confidence dipotong, needs_human."""
from __future__ import annotations

import math
import re

from warden.agents.schemas import Category, Diagnosis, PERMANENT, Recommended as R, Transience

# which recommendations make sense for a category (LLM picks the first hypothesis; the ladder supplies the rest)
FITS: dict[Category, set[R]] = {
    Category.oom_gpu: {R.resume_smaller_batch, R.change_machine_type, R.stop, R.escalate, R.patch_suggest},
    Category.oom_host: {R.resume_fewer_workers, R.change_machine_type, R.stop, R.escalate, R.patch_suggest},
    Category.nan_divergence: {R.rollback_last_good, R.stop, R.escalate, R.patch_suggest},
    Category.nan_input: {R.stop, R.patch_suggest, R.escalate},
    Category.plateau: {R.noop, R.escalate, R.stop, R.rollback_last_good},
    Category.dependency_missing: {R.stop, R.patch_suggest, R.escalate},
    Category.env_broken: {R.stop, R.patch_suggest, R.escalate, R.restart_clean},
    Category.kernel_fallback: {R.stop, R.patch_suggest, R.escalate, R.change_machine_type},
    Category.disk_full: {R.clean_disk, R.resize_disk, R.stop, R.escalate},
    Category.data_error: {R.stop, R.patch_suggest, R.escalate},
    Category.network_transient: {R.resume_same, R.escalate, R.noop},
    Category.preempt: {R.resume_same, R.relocate_zone, R.escalate, R.noop},
    Category.config_error: {R.stop, R.patch_suggest, R.escalate},
    Category.code_bug: {R.stop, R.patch_suggest, R.escalate},
    Category.unknown: {R.escalate, R.noop, R.stop, R.restart_clean, R.kill_and_resume, R.resume_same},
}

RX = {
    Category.oom_gpu: re.compile(r"CUDA out of memory|OutOfMemoryError|CUBLAS_STATUS_ALLOC_FAILED", re.I),
    Category.oom_host: re.compile(r"Out of memory: Killed|oom-kill|MemoryError|Killed\b", re.I),
    Category.nan_divergence: re.compile(r"\bnan\b|\binf\b", re.I),
    Category.nan_input: re.compile(r"contains NaN|Input X contains|\bnan\b", re.I),
    Category.dependency_missing: re.compile(r"ModuleNotFoundError|ImportError|No module named|externally-managed-environment", re.I),
    Category.env_broken: re.compile(r"cannot open shared object|libcud|CUDA driver|not compiled with|version .* incompatible", re.I),
    Category.disk_full: re.compile(r"No space left on device|ENOSPC|Disk quota exceeded", re.I),
    Category.network_transient: re.compile(r"ConnectionReset|Connection refused|timed out|TimeoutError|503|429|EOFError", re.I),
    Category.config_error: re.compile(r"KeyError|unrecognized arguments|invalid choice|AssertionError", re.I),
    Category.code_bug: re.compile(r"Traceback|TypeError|AttributeError|IndexError|ValueError", re.I),
}


def crosscheck(diag: Diagnosis, log_lines: list[str], hb: dict | None) -> dict:
    """Kembalikan {passed: bool, checks: [...], adjusted_confidence, needs_human}."""
    checks: list[dict] = []
    ok_all = True

    def add(name: str, ok: bool, note: str = ""):
        nonlocal ok_all
        checks.append({"check": name, "ok": ok, "note": note}); ok_all = ok_all and ok

    # 1) evidence_lines harus ada dalam rentang
    n = len(log_lines)
    bad = [i for i in diag.evidence_lines if i < 1 or i > n]
    add("evidence_lines_in_range", not bad, f"out of range: {bad}" if bad else "")
    # 2) kutipan harus substring dari baris yang ditunjuk
    pointed = " \n".join(log_lines[i - 1] for i in diag.evidence_lines if 1 <= i <= n)
    bad_q = [q for q in diag.evidence_quotes if q.strip() and q.strip()[:60] not in pointed]
    add("quotes_are_substrings", not bad_q, f"quotes not found: {bad_q[:2]}" if bad_q else "")
    # 3) kategori harus cocok pola pada baris yang ditunjuk ATAU sinyal heartbeat
    rx = RX.get(diag.category)
    if rx is not None:
        hit = bool(rx.search(pointed))
        hb_hit = False
        if hb:
            if diag.category == Category.oom_gpu and hb.get("vram_used_mb") and hb.get("vram_total_mb"):
                hb_hit = hb["vram_used_mb"] / max(hb["vram_total_mb"], 1) >= 0.95
            if diag.category in (Category.nan_divergence, Category.nan_input) and hb.get("loss") is not None:
                hb_hit = isinstance(hb["loss"], float) and (math.isnan(hb["loss"]) or math.isinf(hb["loss"]))
            if diag.category == Category.disk_full and hb.get("disk_avail_gb") is not None:
                hb_hit = hb["disk_avail_gb"] < 1.0
            if diag.category == Category.kernel_fallback and hb.get("step_per_s") is not None and hb.get("baseline_step_per_s"):
                hb_hit = hb["step_per_s"] < 0.2 * hb["baseline_step_per_s"] and (hb.get("gpu_util") or 100) < 30
        add(f"category_pattern:{diag.category}", hit or hb_hit, "" if (hit or hb_hit) else "pattern/heartbeat do not support category")
    elif diag.category == Category.unknown:
        add("unknown_needs_human", diag.needs_human, "unknown requires needs_human")
    # 4) transient dilarang untuk kategori permanen
    if diag.category in PERMANENT:
        add("no_transient_for_permanent", diag.transient_or_permanent != Transience.transient)
    # 5) OOM wajib punya culprit_frame
    if diag.category == Category.oom_gpu:
        add("oom_has_culprit_frame", bool(diag.culprit_frame))
    # 6) the recommended action must be a sensible response to the category (a stop for a preempt, or a resume for a code bug, is not)
    fits = FITS.get(diag.category)
    if fits is not None:
        add("action_fits_category", diag.recommended_action in fits, "" if diag.recommended_action in fits else f"{diag.recommended_action} does not fit {diag.category}")

    adjusted = diag.confidence if ok_all else min(diag.confidence, 0.4)
    return {"passed": ok_all, "checks": checks, "adjusted_confidence": adjusted,
            "needs_human": diag.needs_human or not ok_all}
