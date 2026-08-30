"""Launch the job the take is filmed against, and keep it honest.

Warden launches a real Spot machine on Compute Engine from a spec, trains a real model, hits a real GPU
out-of-memory error at step 600, and recovers. The film script only watches; this is what it watches. The machine is
stopped in the finally block, never deleted, so the disk stays as evidence.

    python -m chaos.film_drill            # launches and blocks until COMPLETE, then stops the machine
    python -m chaos.film_drill --stop-only
Cost: one e2-medium Spot machine for well under an hour (about $0.01) plus a few Gemini calls (about $0.05).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
G = "/home/ubuntu/google-cloud-sdk/bin/gcloud"
P = (ROOT / ".gcp_project").read_text().strip()
B = f"{P}-warden"
os.environ.update({"WARDEN_PROJECT": P, "WARDEN_PROVIDER": "gce", "WARDEN_BUCKET": B})


def _gc(*args: str) -> str:
    return subprocess.run([G, *args, "--project", P], capture_output=True, text=True).stdout.strip()


CORE = _gc("run", "services", "describe", "warden-core", "--region", "us-central1", "--format", "value(status.url)")
SECRET = _gc("secrets", "versions", "access", "latest", "--secret", "warden-ingest-hmac")

from warden.store import firestore as db  # noqa: E402


def post(path: str, key: bytes, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(CORE + path, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "X-Warden-Signature": hmac.new(SECRET.encode(), key, hashlib.sha256).hexdigest()})
    try:
        with urllib.request.urlopen(req, timeout=420) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001 — fail loud, never silently
        extra = getattr(e, "read", lambda: b"")().decode(errors="ignore")[:300] if hasattr(e, "read") else ""
        return {"ok": False, "error": f"{e} {extra}"}


def spec(job_id: str, steps: int = 1500, sleep: float = 0.25) -> dict:
    cmd = (f"bash -c 'gcloud storage cp gs://{B}/demo/toy_bootstrap.sh /opt/toy_bootstrap.sh -q && "
           f"TOY_STEPS={steps} TOY_SLEEP={sleep} TOY_ARGS=\"--oom-at 600\" bash /opt/toy_bootstrap.sh'")
    return {"job_id": job_id, "command": cmd, "machine_type": "e2-medium",
            "zones": ["us-central1-b", "us-central1-c", "us-central1-a"], "spot": True, "disk_gb": 20,
            "expect": {"pred.csv": {"rows": 2000}, "steps": steps}, "budget_cap_usd": 0.5,
            "entry": "toy_train.py", "labels": {"warden-role": "film"}}


def stop(job_id: str) -> None:
    j = db.jobs.get(job_id)
    ref = j.instance_ref if j else ""
    if not ref:
        print("no machine to stop"); return
    zone, name = ref.split("/", 1)
    print(subprocess.run([G, "compute", "instances", "stop", name, "--zone", zone, "--project", P, "-q"],
                         capture_output=True, text=True).stderr.strip() or f"stopped {ref}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default="")
    ap.add_argument("--stop-only", action="store_true")
    ns = ap.parse_args()
    jf = ROOT / "docs/video/.job"
    if ns.stop_only:
        stop(ns.job or jf.read_text().strip()); return 0

    job_id = ns.job or f"demo-{time.strftime('%H%M', time.gmtime())}"
    jf.write_text(job_id)
    s = spec(job_id)
    print(f"launching {job_id} against {CORE}", flush=True)
    r = post("/jobs/launch", json.dumps(s).encode(), s)
    print("launch:", {k: v for k, v in r.items() if k != "attempts"}, flush=True)
    if not r.get("ok"):
        raise SystemExit(f"launch failed: {r}")
    try:
        t0 = time.time()
        while time.time() - t0 < 3600:
            j = db.jobs.get(job_id)
            hb = db.last_heartbeat(job_id)
            print(f"[{time.time() - t0:5.0f}s] {j.status if j else '—'} "
                  f"step={hb.step if hb else '—'} run={hb.run_id if hb else '—'}", flush=True)
            if j and str(j.status) in ("COMPLETE", "FAILED", "ABANDONED"):
                print(f"job finished: {j.status}", flush=True)
                break
            time.sleep(20)
    finally:
        stop(job_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
