"""warden_beat.py — satu berkas, stdlib saja, disalin ke mesin. Dipanggil dari loop training:
    from warden_beat import beat
    beat(phase="train", step=i, loss=float(loss), lr=lr, grad_norm=gn)   # tiap 50 step / ≤120 detik
Menulis /var/lib/warden/<job>/train.json secara atomik; warden-agent yang mengirim ke Warden."""
from __future__ import annotations

import json
import os
import time

_DIR = os.environ.get("WARDEN_DIR", "/var/lib/warden")
_JOB = os.environ.get("WARDEN_JOB", "")
_RUN = os.environ.get("WARDEN_RUN", "")
_last = {"t": 0.0, "step": -1}


def beat(phase: str = "", step: int | None = None, epoch: int | None = None, loss: float | None = None,
         lr: float | None = None, grad_norm: float | None = None, last_ckpt: str = "", every_steps: int = 50,
         max_interval_s: int = 120, **extra) -> None:
    now = time.time()
    if step is not None and _last["step"] >= 0 and step - _last["step"] < every_steps and now - _last["t"] < max_interval_s:
        return
    d = os.path.join(_DIR, _JOB or "default")
    os.makedirs(d, exist_ok=True)
    rec = {"v": 1, "job_id": _JOB, "run_id": _RUN, "ts": now, "phase": phase, "step": step, "epoch": epoch,
           "loss": (None if loss is None else float(loss)), "lr": lr, "grad_norm": (None if grad_norm is None else float(grad_norm)),
           "step_per_s": (None if step is None or _last["step"] < 0 or now == _last["t"] else round((step - _last["step"]) / max(now - _last["t"], 1e-6), 4)),
           "last_ckpt": last_ckpt, "extra": extra}
    tmp = os.path.join(d, "train.json.tmp")
    with open(tmp, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, os.path.join(d, "train.json"))
    with open(os.path.join(d, "denyut.log"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    _last["t"], _last["step"] = now, (step if step is not None else _last["step"])
