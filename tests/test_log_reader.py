"""Catalog #33: the Storage log reader must not depend on Blob.download_as_text(errors=...) — that keyword does not exist in the
installed google-cloud-storage and every production log read failed silently for hours (health 'gcs' red, diagnosis 'unknown')."""
import types
from warden.agents import pipeline as P


class _Blob:
    def __init__(self, data: bytes): self._d = data
    def exists(self): return True
    def download_as_bytes(self): return self._d
    def download_as_text(self, *a, **kw): raise TypeError("download_as_text() got an unexpected keyword argument 'errors'")


class _Bucket:
    def __init__(self, data): self._d = data
    def blob(self, name): return _Blob(self._d)


def test_read_log_tail_uses_bytes(monkeypatch):
    import sys
    fake_storage = types.SimpleNamespace(Client=lambda: types.SimpleNamespace(bucket=lambda b: _Bucket(b"step 1\nstep 2\nCUDA out of memory\xff\n")))
    monkeypatch.setitem(sys.modules, "google.cloud.storage", fake_storage)
    monkeypatch.setattr(P.settings, "bucket", "b")
    lines = P.read_log_tail("j", run_id="r1")
    assert lines[-1].startswith("CUDA out of memory") and len(lines) == 3
