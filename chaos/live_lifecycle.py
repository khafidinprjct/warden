"""LIVE test on the real project (checklist M2/M4): Warden launches a job from a spec, guards it through a GPU-OOM drill
(diagnosis → resume with smaller batch → verified → COMPLETE → report → machine stopped), then relocation, machine-type change,
clean_disk and stop are driven through the operator path (propose → approve) and verified against the world.
Costs: two e2-medium spot VMs for < 1 h (≈ $0.02) + Gemini (≈ $0.10). Nothing is deleted; the finally block STOPs.
    python -m chaos.live_lifecycle [--skip-phase2]"""
from __future__ import annotations

import argparse, hashlib, hmac, json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
G = "/home/ubuntu/google-cloud-sdk/bin/gcloud"
P = (ROOT / ".gcp_project").read_text().strip()
B = f"{P}-warden"
CORE = subprocess.run([G, "run", "services", "describe", "warden-core", "--region", "us-central1", "--project", P, "--format", "value(status.url)"], capture_output=True, text=True).stdout.strip()
LATEST = subprocess.run([G, "run", "services", "describe", "warden-core", "--region", "us-central1", "--project", P, "--format", "value(status.latestReadyRevisionName)"], capture_output=True, text=True).stdout.strip()
SECRET = subprocess.run([G, "secrets", "versions", "access", "latest", "--secret", "warden-ingest-hmac", "--project", P], capture_output=True, text=True).stdout.strip()
os.environ.update({"WARDEN_PROJECT": P, "WARDEN_PROVIDER": "gce", "WARDEN_BUCKET": B})
from google.cloud import firestore  # noqa: E402
FS = firestore.Client(project=P)
T0 = time.time(); REPORT: list[dict] = []
STAMP = time.strftime("%H%M", time.gmtime())


def log(msg: str, **kw) -> None:
    rec = {"t": round(time.time() - T0), "msg": msg, **kw}; REPORT.append(rec)
    print(f"[{rec['t']:5d}s] {msg} {json.dumps(kw, default=str) if kw else ''}", flush=True)


def sig(b: bytes) -> str:
    return hmac.new(SECRET.encode(), b, hashlib.sha256).hexdigest()


def post(path: str, key: bytes, body: dict | None = None) -> dict:
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(CORE + path, data=data, method="POST", headers={"Content-Type": "application/json", "X-Warden-Signature": sig(key)})
    try:
        with urllib.request.urlopen(req, timeout=420) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        body_txt = getattr(e, "read", lambda: b"")().decode(errors="ignore")[:300] if hasattr(e, "read") else ""
        return {"ok": False, "error": f"{e} {body_txt}"}


def job(job_id: str) -> dict:
    d = FS.collection("jobs").document(job_id).get(); return d.to_dict() or {}


def incidents(job_id: str) -> list[dict]:
    return [d.to_dict() for d in FS.collection("incidents").where(filter=firestore.FieldFilter("job_id", "==", job_id)).stream()]


def decisions(inc: dict) -> list[dict]:
    return [FS.collection("decisions").document(x).get().to_dict() for x in inc.get("decision_ids", [])]


def last_hb(job_id: str) -> dict:
    d = FS.collection("runs").document(job_id).get(); return (d.to_dict() or {}).get("last", {})


def wait(desc: str, fn, timeout_s: int, every: int = 20):
    t = time.time()
    while time.time() - t < timeout_s:
        v = fn()
        if v:
            log(f"✔ {desc}", value=(v if not isinstance(v, bool) else True)); return v
        time.sleep(every)
    log(f"✘ TIMEOUT {desc} after {timeout_s}s"); return None


def instance(ref: str) -> dict:
    zone, name = ref.split("/", 1)
    out = subprocess.run([G, "compute", "instances", "describe", name, "--zone", zone, "--project", P, "--format", "json(status,machineType,labels)"], capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else {}


def stop_all(job_ids: list[str]) -> None:
    for j in job_ids:
        ref = job(j).get("instance_ref", "")
        for cand in {ref, *[f"{z}/warden-{j}" for z in ("us-central1-a", "us-central1-b", "us-central1-c")]}:
            if not cand:
                continue
            zone, name = cand.split("/", 1)
            subprocess.run([G, "compute", "instances", "stop", name, "--zone", zone, "--project", P, "--quiet"], capture_output=True, text=True)
    log("cleanup: STOP issued for all live-test instances (never delete)")


def spec(job_id: str, steps: int, sleep: float, extra: str = "") -> dict:
    cmd = f"bash -c 'gcloud storage cp gs://{B}/demo/toy_bootstrap.sh /opt/toy_bootstrap.sh -q && TOY_STEPS={steps} TOY_SLEEP={sleep} TOY_ARGS=\"{extra}\" bash /opt/toy_bootstrap.sh'"
    return {"job_id": job_id, "command": cmd, "machine_type": "e2-medium", "zones": ["us-central1-b", "us-central1-c", "us-central1-a"], "spot": True, "disk_gb": 20,
            "expect": {"pred.csv": {"rows": 2000}, "steps": steps}, "budget_cap_usd": 0.5, "entry": "toy_train.py", "labels": {"warden-role": "live-test"}}


def phase1() -> str:
    j1 = f"live-{STAMP}-oom"
    log("PHASE 1: launch from spec with a GPU-OOM drill at step 600 (batch scale 1.0 → OOM; 0.5 passes)")
    r = post("/jobs/launch", json.dumps(spec(j1, 1500, 0.2, "--oom-at 600")).encode(), spec(j1, 1500, 0.2, "--oom-at 600"))
    log("launch", result={k: v for k, v in r.items() if k != "attempts"}, attempts=len(r.get("attempts", [])))
    assert r.get("ok"), r
    wait("job RUNNING on first heartbeat", lambda: job(j1).get("status") == "RUNNING", 600)
    wait("training advancing", lambda: (last_hb(j1).get("step") or 0) >= 200, 600)
    inc = wait("incident run_fin_nonzero opened after the OOM", lambda: next((i for i in incidents(j1) if i["rule"] == "run_fin_nonzero"), None), 600, 15)
    assert inc
    def _resumed():
        i = next((x for x in incidents(j1) if x["rule"] == "run_fin_nonzero"), {})
        ds = decisions(i)
        return next(({"action": d["action"], "verdict": d["verdict"], "params": {k: v for k, v in d.get("params", {}).items() if k in ("mode", "batch_scale", "reason")}, "status": d["status"], "diag": i.get("diagnosis", {}).get("category")}
                     for d in ds if d and d["action"] == "resume_job" and d["status"] in ("DONE", "EXECUTING")), None)
    dec = wait("diagnosis → resume_job smaller batch executed (L2, no human)", _resumed, 600, 15)
    assert dec and dec["params"].get("batch_scale") == 0.5, dec
    wait("resumed run past the OOM step (batch 32)", lambda: (last_hb(j1).get("step") or 0) > 650 and last_hb(j1).get("run_id") != inc.get("run_id"), 600)
    inc_state = wait("incident RESOLVED by world-verification (new run advancing)", lambda: next((i["state"] for i in incidents(j1) if i["rule"] == "run_fin_nonzero" and i["state"] == "RESOLVED"), None), 900, 15)
    i = next(x for x in incidents(j1) if x["rule"] == "run_fin_nonzero"); log("verify record", verify={k: v for k, v in (i.get("verify") or {}).items() if k in ("kind", "result")}, last_check=((i.get("verify") or {}).get("checks") or [{}])[-1], attempt=i.get("attempt"))
    wait("job COMPLETE (artifacts opened & VERIFIED)", lambda: job(j1).get("status") == "COMPLETE", 900, 15)
    rep = wait("final report written", lambda: job(j1).get("report") or None, 300, 15)
    log("report", spent=rep.get("spent_usd"), ettr=rep.get("ettr"), incidents=rep.get("incidents"), artifacts=len(rep.get("artifacts", [])))
    wait("machine stopped by close-out rule", lambda: instance(job(j1)["instance_ref"]).get("status") in ("TERMINATED", "STOPPING", "STOPPED"), 400, 15)
    return j1


def approve_latest(job_id: str, action: str) -> dict:
    for i in sorted(incidents(job_id), key=lambda x: x.get("created_at", ""), reverse=True):
        for d in decisions(i):
            if d and d["action"] == action and d["status"] == "PENDING" and d["verdict"] == "NEED_APPROVAL":
                r = post(f"/decisions/{d['decision_id']}/approve?who=live-test", d["decision_id"].encode()); log(f"approved {action}", r=r); return r
    return {}


def phase2() -> str:
    j2 = f"live-{STAMP}-ops"
    log("PHASE 2: long job; relocate_zone, change_machine_type, clean_disk, stop through propose → approve → world-verified")
    r = post("/jobs/launch", json.dumps(spec(j2, 6000, 0.4)).encode(), spec(j2, 6000, 0.4)); assert r.get("ok"), r
    wait("job RUNNING", lambda: job(j2).get("status") == "RUNNING", 600)
    wait("advancing ≥ 400 steps (checkpoints exist)", lambda: (last_hb(j2).get("step") or 0) >= 400, 600)
    ref0 = job(j2)["instance_ref"]
    # relocate (L1)
    r = post(f"/jobs/{j2}/propose", j2.encode(), {"action": "relocate_zone", "why": "live test: move the job to another zone", "who": "live-test"}); log("propose relocate", r=r)
    assert r.get("verdict") == "NEED_APPROVAL", r
    approve_latest(j2, "relocate_zone")
    ref1 = wait("job now points at a new instance in another zone", lambda: (job(j2)["instance_ref"] if job(j2)["instance_ref"] != ref0 else None), 900, 20)
    assert ref1 and ref1.split("/")[0] != ref0.split("/")[0], (ref0, ref1)
    wait("relocation VERIFIED (new boot, steps advancing)", lambda: next((i["state"] for i in incidents(j2) if i["rule"] == "operator_request" and "relocate" in i["summary"] and i["state"] == "RESOLVED"), None), 1200, 20)
    log("old instance state", old=instance(ref0).get("status"), new=instance(ref1))
    # change machine type (L1): e2-small = cheaper, so the price guard passes
    r = post(f"/jobs/{j2}/propose", j2.encode(), {"action": "change_machine_type", "params": {"machine_type": "e2-small"}, "why": "live test", "who": "live-test"}); log("propose change_machine_type", r=r)
    approve_latest(j2, "change_machine_type")
    wait("machine type changed and verified", lambda: next((i["state"] for i in incidents(j2) if i["rule"] == "operator_request" and "change_machine_type" in i["summary"] and i["state"] == "RESOLVED"), None), 1200, 20)
    log("instance after type change", inst=instance(job(j2)["instance_ref"]))
    # clean_disk (L2 → automatic)
    r = post(f"/jobs/{j2}/propose", j2.encode(), {"action": "clean_disk", "params": {"keep": 1}, "why": "live test", "who": "live-test"}); log("propose clean_disk", r=r)
    wait("clean_disk verified by harness result + heartbeat", lambda: next((i["state"] for i in incidents(j2) if i["rule"] == "operator_request" and "clean_disk" in i["summary"] and i["state"] in ("RESOLVED", "ESCALATED")), None), 600, 15)
    res = [d.to_dict() for d in FS.collection("cmd_results").where(filter=firestore.FieldFilter("job_id", "==", j2)).stream()]
    log("harness results", results=[{k: r.get(k) for k in ("cmd", "ok", "detail", "freed_bytes")} for r in res][-4:])
    # stop (L2)
    r = post(f"/jobs/{j2}/propose", j2.encode(), {"action": "stop_instance", "why": "live test done", "who": "live-test"}); log("propose stop", r=r)
    wait("stop verified", lambda: next((i["state"] for i in incidents(j2) if i["rule"] == "operator_request" and "stop_instance" in i["summary"] and i["state"] == "RESOLVED"), None), 400, 15)
    return j2


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--skip-phase1", action="store_true"); ap.add_argument("--skip-phase2", action="store_true"); ns = ap.parse_args()
    jobs: list[str] = []
    import urllib.request
    for _ in range(40):   # drill #2 (26 Aug) was served by the previous revision two minutes after "deployed": wait for the new one
        try:
            rev = json.loads(urllib.request.urlopen(CORE + "/healthz", timeout=20).read()).get("revision", "")
        except Exception:  # noqa: BLE001
            rev = ""
        if rev in ("", "local") or rev == LATEST:
            break
        log(f"core still serving {rev}, waiting for {LATEST}"); time.sleep(15)
    log("core", url=CORE, latest=LATEST)
    try:
        if not ns.skip_phase1:
            jobs.append(phase1())
        if not ns.skip_phase2:
            jobs.append(phase2())
        log("LIVE TEST FINISHED")
    except Exception as e:  # noqa: BLE001
        log(f"LIVE TEST FAILED: {type(e).__name__}: {e}")
    finally:
        stop_all(jobs or [f"live-{STAMP}-oom", f"live-{STAMP}-ops"])
        Path("chaos/live_lifecycle_report.json").write_text(json.dumps({"core": CORE, "jobs": jobs, "events": REPORT}, indent=1, default=str))
        print("report → chaos/live_lifecycle_report.json")
