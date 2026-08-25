"""Verifier artefak (P3: bukti = membuka). Plugin per tipe; expectation model per job; 'ukur saat penulis diam'."""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class VerifyResult:
    ok: bool
    checks: list[dict] = field(default_factory=list)
    corrupt_reason: str = ""
    bytes: int = 0
    sha256: str = ""
    meta: dict = field(default_factory=dict)

    def add(self, name: str, ok: bool, note: str = "") -> None:
        self.checks.append({"check": name, "ok": ok, "note": note})
        if not ok:
            self.ok = False
            self.corrupt_reason = self.corrupt_reason or f"{name}: {note}"


def sha256_file(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def writer_quiet(p: str | Path, open_writers: list[str] | None = None, min_age_s: int = 90) -> tuple[bool, str]:
    """Hanya ukur saat penulis diam: tidak ada .tmp/.partial sejenis, tidak terdaftar open_writers, mtime ≥ min_age."""
    p = Path(p)
    if open_writers and p.name in open_writers:
        return False, "listed as being written (open_writers)"
    if p.with_suffix(p.suffix + ".tmp").exists() or p.with_suffix(p.suffix + ".partial").exists():
        return False, ".tmp/.partial file present"
    import time
    if time.time() - p.stat().st_mtime < min_age_s:
        return False, f"mtime < {min_age_s} s"
    return True, ""


def verify_csv(p: Path, expect: dict, r: VerifyResult) -> None:
    import csv
    with open(p, newline="") as f:
        rows = list(csv.reader(f))
    r.add("csv_parse", len(rows) >= 1, "empty")
    if not rows:
        return
    header = rows[0]; body = rows[1:]
    if expect.get("columns"):
        r.add("csv_header", header == expect["columns"], f"{header} != {expect['columns']}")
    if expect.get("rows"):
        r.add("csv_rows", len(body) == expect["rows"], f"{len(body)} != {expect['rows']}")
    widths = {len(x) for x in body}
    r.add("csv_uniform_width", len(widths) <= 1, f"inconsistent row widths {widths}")
    nan_cells = sum(1 for row in body for c in row if c.strip().lower() in ("nan", "inf", "-inf", ""))
    r.add("csv_no_nan", nan_cells == 0, f"{nan_cells} NaN/empty cells")
    for col in expect.get("range01_columns", []):
        if col in header:
            j = header.index(col)
            bad = [row[j] for row in body if row[j].strip() and not (0.0 <= float(row[j]) <= 1.0)]
            r.add(f"csv_range01:{col}", not bad, f"{len(bad)} values outside [0,1]")
    if expect.get("id_column") and expect.get("ids"):
        j = header.index(expect["id_column"]); ids = [row[j] for row in body]
        r.add("csv_ids_match", ids == list(expect["ids"]), "IDs do not match samples")
    r.meta.update(rows=len(body), columns=header)


def verify_json(p: Path, expect: dict, r: VerifyResult) -> None:
    try:
        d = json.load(open(p)); r.add("json_parse", True)
    except Exception as e:
        r.add("json_parse", False, str(e)[:80]); return
    for k in expect.get("keys", []):
        r.add(f"json_key:{k}", k in d, "key missing")


def verify_jsonl(p: Path, expect: dict, r: VerifyResult) -> None:
    raw = open(p, "rb").read()
    r.add("jsonl_trailing_newline", raw.endswith(b"\n"), "last byte is not a newline (truncated write?)")
    lines = raw.decode(errors="ignore").splitlines()
    n = len(lines)
    try:
        json.loads(lines[-1]) if lines else None; r.add("jsonl_last_line", True)
    except Exception:
        r.add("jsonl_last_line", False, "last line is not valid JSON")
    if expect.get("min_rows"):
        r.add("jsonl_min_rows", n >= expect["min_rows"], f"{n} < {expect['min_rows']}")
    r.meta.update(rows=n)


def verify_npz(p: Path, expect: dict, r: VerifyResult) -> None:
    import numpy as np
    try:
        z = np.load(p, allow_pickle=False); keys = list(z.keys()); r.add("npz_load", True)
    except Exception as e:
        r.add("npz_load", False, str(e)[:80]); return
    for k in expect.get("keys", []):
        r.add(f"npz_key:{k}", k in keys, "key missing")
    bad = [k for k in keys if z[k].dtype.kind == "f" and not np.isfinite(z[k]).all()]
    r.add("npz_finite", not bad, f"non-finite in {bad[:3]}")
    r.meta.update(keys=keys[:20])


def verify_parquet(p: Path, expect: dict, r: VerifyResult) -> None:
    import pyarrow.parquet as pq
    with open(p, "rb") as f:
        f.seek(-4, 2); r.add("parquet_footer", f.read(4) == b"PAR1", "PAR1 footer missing (truncated)")
    try:
        md = pq.read_metadata(p); r.add("parquet_metadata", True); r.meta.update(rows=md.num_rows, row_groups=md.num_row_groups)
        if expect.get("rows"):
            r.add("parquet_rows", md.num_rows == expect["rows"], f"{md.num_rows} != {expect['rows']}")
    except Exception as e:
        r.add("parquet_metadata", False, str(e)[:80])


def verify_torch(p: Path, expect: dict, r: VerifyResult) -> None:
    """Checkpoint torch: zip utuh → torch.load(cpu, weights_only) → kunci wajib → isfinite sampel → step monoton."""
    with open(p, "rb") as f:
        r.add("torch_zip_magic", f.read(2) == b"PK", "not a torch ≥1.6 zip format (truncated/legacy format)")
    try:
        with zipfile.ZipFile(p) as z:
            bad = z.testzip(); r.add("torch_zip_integrity", bad is None, f"corrupt entry: {bad}")
    except Exception as e:
        r.add("torch_zip_integrity", False, str(e)[:80]); return
    try:
        import torch
        obj = torch.load(p, map_location="cpu", weights_only=True); r.add("torch_load", True)
    except ImportError:
        r.add("torch_load", True, "torch not installed in verifier — zip integrity only"); return
    except Exception as e:
        r.add("torch_load", False, str(e)[:100]); return
    sd = obj.get("state_dict", obj) if isinstance(obj, dict) else {}
    for k in expect.get("keys", []):
        r.add(f"torch_key:{k}", k in obj if isinstance(obj, dict) else False, "key missing")
    import torch as _t
    tens = [v for v in (sd.values() if isinstance(sd, dict) else []) if hasattr(v, "dtype") and v.is_floating_point()]
    sample = tens[:: max(1, len(tens) // 20)] if tens else []
    nonfinite = [i for i, t in enumerate(sample) if not _t.isfinite(t).all()]
    r.add("torch_finite_sample", not nonfinite, f"{len(nonfinite)} non-finite tensors")
    step = obj.get("step") or obj.get("global_step") or obj.get("epoch") if isinstance(obj, dict) else None
    if step is not None and expect.get("min_step") is not None:
        r.add("torch_step_monotonic", step >= expect["min_step"], f"step {step} < {expect['min_step']}")
    r.meta.update(n_tensors=len(tens), step=step)


PLUGINS = {".csv": verify_csv, ".json": verify_json, ".jsonl": verify_jsonl, ".npz": verify_npz, ".parquet": verify_parquet,
           ".pt": verify_torch, ".pth": verify_torch, ".ckpt": verify_torch}


def verify(path: str | Path, expect: dict | None = None, declared_sha256: str = "", prev_sha256: str = "",
           open_writers: list[str] | None = None, min_age_s: int = 90) -> VerifyResult:
    p = Path(path); expect = expect or {}
    r = VerifyResult(ok=True)
    if not p.exists():
        r.add("exists", False, "file not found"); return r
    quiet, why = writer_quiet(p, open_writers, min_age_s)
    if not quiet:
        r.add("writer_quiet", False, why); r.meta["retry"] = True; return r
    r.bytes = p.stat().st_size; r.add("size_nonzero", r.bytes > 0, "0 byte")
    r.sha256 = sha256_file(p)
    if declared_sha256:
        r.add("sha256_matches_declared", r.sha256 == declared_sha256, "sha256 ≠ declared in RUN_FIN")
    if prev_sha256:
        r.add("not_identical_to_previous", r.sha256 != prev_sha256, "identical to previous artifact (stale copy?)")
    if expect.get("bytes"):
        tol = expect.get("tol", 0.10); lo, hi = expect["bytes"] * (1 - tol), expect["bytes"] * (1 + tol)
        r.add("size_vs_expect", lo <= r.bytes <= hi, f"{r.bytes} outside [{int(lo)},{int(hi)}] ({r.bytes/expect['bytes']*100:.0f}% of expected)")
    fn = PLUGINS.get(p.suffix.lower())
    if fn:
        try:
            fn(p, expect, r)
        except Exception as e:
            r.add(f"plugin:{p.suffix}", False, f"{type(e).__name__}: {str(e)[:80]}")
    else:
        r.add("plugin", True, "unknown type — size+sha only")
    return r
