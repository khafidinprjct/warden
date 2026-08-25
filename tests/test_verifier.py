import json, os, time
import numpy as np
from warden.verifier.base import verify


def _old(p):  # mtime dibuat lama supaya 'penulis diam'
    os.utime(p, (time.time() - 600, time.time() - 600)); return p


def test_csv_ok_and_truncated(tmp_path):
    p = tmp_path / "pred.csv"; p.write_text("ID,TargetF1,TargetRAUC\n" + "".join(f"{i},1,0.5\n" for i in range(10)))
    _old(p)
    r = verify(p, {"columns": ["ID", "TargetF1", "TargetRAUC"], "rows": 10, "range01_columns": ["TargetRAUC"]})
    assert r.ok, r.checks
    q = tmp_path / "bad.csv"; q.write_text("ID,TargetF1,TargetRAUC\n1,1,nan\n2,0,0.3\n"); _old(q)
    r2 = verify(q, {"rows": 10})
    assert not r2.ok and any(c["check"] in ("csv_rows", "csv_no_nan") and not c["ok"] for c in r2.checks)


def test_writer_active_is_retry(tmp_path):
    p = tmp_path / "x.csv"; p.write_text("a\n1\n")      # mtime baru → belum boleh diukur
    r = verify(p, {})
    assert not r.ok and r.meta.get("retry") is True


def test_size_vs_expect_15pct(tmp_path):
    p = tmp_path / "ckpt.bin"; p.write_bytes(b"x" * 150); _old(p)
    r = verify(p, {"bytes": 1000})
    assert not r.ok and any(c["check"] == "size_vs_expect" and not c["ok"] for c in r.checks)


def test_identical_to_previous_flagged(tmp_path):
    p = tmp_path / "a.json"; p.write_text('{"k":1}'); _old(p)
    r1 = verify(p, {"keys": ["k"]}); assert r1.ok
    r2 = verify(p, {"keys": ["k"]}, prev_sha256=r1.sha256)
    assert not r2.ok and r2.corrupt_reason.startswith("not_identical")


def test_npz_nonfinite(tmp_path):
    p = tmp_path / "o.npz"; np.savez(p, a=np.array([1.0, np.nan])); _old(p)
    assert not verify(p, {"keys": ["a"]}).ok


def test_jsonl_truncated(tmp_path):
    p = tmp_path / "e.jsonl"; p.write_text('{"a":1}\n{"a":2'); _old(p)
    r = verify(p, {"min_rows": 2}); assert not r.ok


def test_torch_zip_truncated(tmp_path):
    import zipfile
    p = tmp_path / "m.pt"
    with zipfile.ZipFile(p, "w") as z: z.writestr("data.pkl", b"\x80\x04K\x01.")
    raw = p.read_bytes(); p.write_bytes(raw[: int(len(raw) * 0.6)]); _old(p)
    r = verify(p, {}); assert not r.ok
