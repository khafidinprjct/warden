from datetime import timedelta
from warden.agents.charts import render_curves
from warden.core.models import Heartbeat, now


def test_render_curves_png_and_nan_marker():
    hbs = [Heartbeat(job_id="j", ts=now() - timedelta(minutes=30 - i), step=i * 50, loss=(float("nan") if i == 20 else 1 / (i + 1)), grad_norm=1.0, step_per_s=2.0, disk_avail_gb=20 - i * 0.1) for i in range(30)]
    png = render_curves(hbs, "test")
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000
    assert render_curves(hbs[:3]) is None
