from warden.config import settings
from warden.providers.fake_gce import FakeGCE

_fake: FakeGCE | None = None


def compute():
    """Pilih lapisan compute dari konfigurasi. 'fake' = in-memory (tes/latihan)."""
    global _fake
    if settings.provider == "gce":
        from warden.providers.gce import GCE
        return GCE()
    if _fake is None:
        _fake = FakeGCE()
    return _fake
