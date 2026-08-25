"""Multimodal evidence (checklist C5): render the training curves from heartbeats as a PNG so the Diagnostician can *look* at a
plateau, a divergence or a throughput collapse when the numbers alone are ambiguous. Matplotlib (Agg), no display."""
from __future__ import annotations

import io
from typing import Any


def render_curves(hbs: list[Any], title: str = "") -> bytes | None:
    """PNG of loss / grad_norm / step rate / disk over heartbeats (oldest first). None when there is nothing to draw."""
    pts = [h for h in hbs if h.step is not None]
    if len(pts) < 4:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001
        return None
    steps = [h.step for h in pts]
    series = [("loss", [h.loss for h in pts]), ("grad_norm", [h.grad_norm for h in pts]), ("step/s", [h.step_per_s for h in pts]), ("disk free GB", [h.disk_avail_gb for h in pts])]
    series = [(n, v) for n, v in series if any(x is not None for x in v)]
    if not series:
        return None
    fig, axes = plt.subplots(len(series), 1, figsize=(8, 2.2 * len(series)), sharex=True)
    axes = list(axes) if len(series) > 1 else [axes]
    for ax, (name, vals) in zip(axes, series):
        xs = [s for s, v in zip(steps, vals) if v is not None]; ys = [v for v in vals if v is not None]
        ax.plot(xs, ys, marker=".", linewidth=1.2)
        ax.set_ylabel(name); ax.grid(alpha=0.3)
        bad = [s for s, v in zip(steps, vals) if v is not None and (v != v or v in (float("inf"), float("-inf")))]
        for b in bad:
            ax.axvline(b, color="red", linestyle="--", linewidth=0.8)
    axes[-1].set_xlabel("step")
    if title:
        fig.suptitle(title)
    fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110); plt.close(fig)
    return buf.getvalue()
