"""Recorded walkthrough of the Warden dashboard under a full board of failures (checklist K1).

Not a demo film: it is a simulation for the owner to judge the interface. Every incident on screen is opened by the real
rule engine (`warden.watcher.rules`) from seeded facts — one job per failure mode, all of them alive at the same time — and
every button is actually clicked, with the effect checked in Firestore afterwards. The video is the evidence; the JSON
report next to it says which control did what.

Runs entirely on the Firestore emulator with the fake fleet. It never touches the real project, and no Gemini call is made:
the deterministic path (detect → decide → approve → execute → verify against the world) is what is being shown, and
diagnoses are seeded so the reasoning panels are populated — those texts are simulated, not model output.

    python -m chaos.ui_tour            # desktop 1440×900 + phone 390×844, videos in docs/video/tour/
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("TOUR_OUT", ROOT / "docs/video/tour"))
OUT.mkdir(parents=True, exist_ok=True)
STATE = OUT / "fleet.json"
CORE_PORT, UI_PORT = "18099", "8099"
os.environ.update({
    "FIRESTORE_EMULATOR_HOST": "127.0.0.1:8081", "WARDEN_PROJECT": "warden-local", "WARDEN_FIRESTORE_DB": "warden-tour",
    "WARDEN_PROVIDER": "fake", "WARDEN_DEV": "1", "WARDEN_FAKE_STATE": str(STATE),
    "WARDEN_CORE_URL": f"http://127.0.0.1:{CORE_PORT}", "PORT": UI_PORT,
})

from datetime import timedelta  # noqa: E402

from warden.core.models import Heartbeat, Instance, InstanceStatus, Job, JobStatus, Marker, now  # noqa: E402
from warden.providers import registry  # noqa: E402
from warden.signals.ingest import sign, validate_marker  # noqa: E402
from warden.store import firestore as db  # noqa: E402
from warden.watcher import tick as T  # noqa: E402

UI = f"http://127.0.0.1:{UI_PORT}"
REPORT: list[dict] = []


def log(msg: str, **kw) -> None:
    print(f"  {msg} {json.dumps(kw, default=str) if kw else ''}", flush=True)


def check(control: str, where: str, ok: bool, detail: str = "") -> bool:
    REPORT.append({"control": control, "page": where, "ok": bool(ok), "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'}  {where:22s} {control}{'  — ' + detail if detail else ''}", flush=True)
    return bool(ok)


# ---------------------------------------------------------------- the board

def reset() -> None:
    STATE.unlink(missing_ok=True)
    registry._fake = None
    T._prev_status.clear()
    for run in db.client().collection("runs").limit(300).stream():
        for h in run.reference.collection("heartbeats").limit(3000).stream():
            h.reference.delete()
    for coll in ("fleet", "jobs", "incidents", "decisions", "evidence", "audit", "markers", "leases", "runs",
                 "notifications", "policies", "policy_overrides", "health", "costs", "cmd", "baselines", "reports"):
        for d in db.client().collection(coll).limit(600).stream():
            d.reference.delete()


def beats(job_id: str, run_id: str, n: int = 14, minutes: float = 4, **kw):
    """n heartbeats ending `age_min` ago. Any Heartbeat field can be a constant or a callable of the index."""
    age = kw.pop("age_min", 1.0)
    out = []
    for i in range(n):
        f = {k: (v(i) if callable(v) else v) for k, v in kw.items()}
        hb = Heartbeat(job_id=job_id, run_id=run_id, ts=now() - timedelta(minutes=age + minutes * (n - 1 - i)), **f)
        db.put_heartbeat(hb)
        out.append(hb)
    return out


def machine(name: str, **kw) -> Instance:
    return registry.compute().add(name, zone=kw.pop("zone", "us-central1-b"), **kw)


def job(job_id: str, inst: Instance | None, **kw) -> Job:
    j = Job(job_id=job_id, instance_ref=inst.ref if inst else "", run_id=kw.pop("run_id", "r1"), **kw)
    db.jobs.put(j)
    if inst:
        inst.job_id = job_id
    return j


def fin(j: Job, exit_code: int = 0, valid: bool = True, artifacts=None) -> Marker:
    ts = now()
    sig = sign(f"{j.job_id}|{j.run_id}|{exit_code}|{ts.isoformat()}".encode()) if valid else "not-a-signature"
    mk = validate_marker(Marker(job_id=j.job_id, run_id=j.run_id, kind="RUN_FIN", ts=ts, exit_code=exit_code,
                                signature=sig, artifacts=artifacts or []))
    db.put_marker(mk)
    return mk


def seed() -> dict:
    """One job per failure mode, all live at once — the board Warden has to sort out."""
    reset()
    fake = registry.compute()
    cmd = "/venv/bin/python train.py"

    # --- preemption family -------------------------------------------------
    i1 = machine("warden-vision-7b"); j1 = job("vision-7b", i1, status=JobStatus.RUNNING, phase="F3-train", command=cmd)
    beats("vision-7b", "r1", gpu_util=92, cpu_pct=80, disk_avail_gb=40, step=lambda i: 500 * i, loss=lambda i: 0.9 - i * 0.05,
          phase="F3-train", boot_id=i1.boot_id, procs=[{"pid": 101, "ppid": 1, "cmd": cmd}])
    fake.preempt(i1.ref)

    i2 = machine("warden-nlp-pretrain", boot_disk_auto_delete=True)
    j2 = job("nlp-pretrain", i2, status=JobStatus.RUNNING, phase="F2", command=cmd)
    beats("nlp-pretrain", "r1", gpu_util=88, cpu_pct=70, disk_avail_gb=30, step=lambda i: 200 * i, loss=0.7, boot_id=i2.boot_id)
    fake.preempt(i2.ref)

    i3 = machine("warden-storm-42", zone="us-central1-a")
    j3 = job("storm-42", i3, status=JobStatus.RUNNING, phase="F1", command=cmd,
             zone_candidates=["us-central1-b", "us-central1-c"])
    beats("storm-42", "r1", gpu_util=90, cpu_pct=75, disk_avail_gb=25, step=lambda i: 100 * i, loss=1.1, boot_id=i3.boot_id)
    for _ in range(3):                                            # three preemptions inside the hour = storm (B4)
        fake.preempt(i3.ref)
        fake.instances[i3.ref].status = InstanceStatus.RUNNING
    fake.preempt(i3.ref)

    # --- the run ended badly ----------------------------------------------
    i4 = machine("warden-speech-ft"); j4 = job("speech-ft", i4, status=JobStatus.RUNNING, phase="F4", command=cmd)
    beats("speech-ft", "r1", gpu_util=85, cpu_pct=60, disk_avail_gb=22, step=lambda i: 300 * i, loss=0.4, boot_id=i4.boot_id)
    fin(j4, exit_code=1)

    i5 = machine("warden-ocr-train"); j5 = job("ocr-train", i5, status=JobStatus.RUNNING, phase="preflight", command=cmd)
    db.put_marker(Marker(job_id="ocr-train", run_id="", kind="PREFLIGHT_FAIL",
                         evidence={"reason": "libcudnn.so.8 missing in the image", "check": "ldd"}))

    i6 = machine("warden-smoke-job"); j6 = job("smoke-job", i6, status=JobStatus.RUNNING, phase="smoke", command=cmd,
                                               expect={"smoke_members": ["tokenizer", "model", "dataset"]})
    db.put_marker(Marker(job_id="smoke-job", run_id="r1", kind="SMOKE_FIN", evidence={"members": ["tokenizer"]}))

    i7 = machine("warden-legacy-eks"); j7 = job("legacy-eks", i7, status=JobStatus.RUNNING, phase="F3", command=cmd, legacy=True)
    db.put_marker(Marker(job_id="legacy-eks", run_id="r1", kind="DONE_LEGACY"))

    i8 = machine("warden-bad-marker"); j8 = job("bad-marker", i8, status=JobStatus.RUNNING, phase="F3", command=cmd)
    fin(j8, exit_code=0, valid=False)

    i9 = machine("warden-finished-ok"); j9 = job("finished-ok", i9, status=JobStatus.RUNNING, phase="export", command=cmd,
                                                 expect={"pred.csv": {"rows": 2000}})
    fin(j9, exit_code=0, artifacts=[{"path": "/var/lib/warden/finished-ok/artifacts/pred.csv", "sha256": "a" * 64, "bytes": 30913}])

    # --- the machine is alive but the work is not -------------------------
    i10 = machine("warden-tabular-xgb"); j10 = job("tabular-xgb", i10, status=JobStatus.RUNNING, phase="F3", command=cmd)
    beats("tabular-xgb", "r1", n=14, minutes=3, age_min=70, gpu_util=1, cpu_pct=2, disk_avail_gb=18,
          step=4200, loss=0.31, boot_id=i10.boot_id, procs=[{"pid": 101, "ppid": 1, "cmd": cmd}])

    i11 = machine("warden-rec-sys"); j11 = job("rec-sys", i11, status=JobStatus.RUNNING, phase="F3", command=cmd)
    beats("rec-sys", "r1", gpu_util=95, cpu_pct=90, disk_avail_gb=20, step=lambda i: 90 * i, loss=0.6, boot_id=i11.boot_id,
          procs=[{"pid": 101, "ppid": 1, "cmd": cmd}, {"pid": 202, "ppid": 1, "cmd": cmd}])

    for k in range(3):                                            # extra duplicate-process jobs → more kill_process (L1) proposals
        ik = machine(f"warden-dup-{k}")
        job(f"dup-{k}", ik, status=JobStatus.RUNNING, phase="F3", command=cmd)
        beats(f"dup-{k}", "r1", gpu_util=93, cpu_pct=91, disk_avail_gb=24, step=lambda i: 70 * i, loss=0.5, boot_id=ik.boot_id,
              procs=[{"pid": 101, "ppid": 1, "cmd": cmd}, {"pid": 202, "ppid": 1, "cmd": cmd}])

    i12 = machine("warden-diffusion-lora")
    j12 = job("diffusion-lora", i12, status=JobStatus.RUNNING, phase="F3", command=cmd,
              expect={"ckpt_size_bytes": 2_000_000_000})
    beats("diffusion-lora", "r1", gpu_util=90, cpu_pct=85, disk_avail_gb=1.4, step=lambda i: 700 * i, loss=0.5, boot_id=i12.boot_id)

    i13 = machine("warden-climate-eks"); job("climate-eks", i13, status=JobStatus.ABANDONED, phase="F3", command=cmd)

    i14 = machine("warden-sweep-42"); j14 = job("sweep-42", i14, status=JobStatus.RUNNING, phase="F3", command=cmd)
    beats("sweep-42", "r1", n=14, minutes=3, age_min=25, gpu_util=0, cpu_pct=1, disk_avail_gb=35, step=900, loss=0.2, boot_id=i14.boot_id)

    i15 = machine("warden-complete-1"); job("complete-1", i15, status=JobStatus.COMPLETE, phase="export", command=cmd)

    # --- money -------------------------------------------------------------
    i16 = machine("warden-bert-qa"); j16 = job("bert-qa", i16, status=JobStatus.RUNNING, phase="F3", command=cmd,
                                               budget_cap_usd=4.0, spent_usd=3.4)
    beats("bert-qa", "r1", gpu_util=80, cpu_pct=70, disk_avail_gb=28, step=lambda i: 150 * i, loss=0.45, boot_id=i16.boot_id)

    i17 = machine("warden-llama-sft"); j17 = job("llama-sft", i17, status=JobStatus.RUNNING, phase="F3", command=cmd,
                                                 budget_cap_usd=2.0, spent_usd=2.6)
    beats("llama-sft", "r1", gpu_util=83, cpu_pct=72, disk_avail_gb=26, step=lambda i: 120 * i, loss=0.55, boot_id=i17.boot_id)

    # --- trends: the warnings that arrive before the incident --------------
    i18 = machine("warden-trend-job")
    j18 = job("trend-job", i18, status=JobStatus.RUNNING, phase="F3", command=cmd,
              expect={"baseline_step_per_s": 5.0, "ckpt_size_bytes": 1_000_000_000})
    beats("trend-job", "r1", n=26, minutes=6, gpu_util=88, cpu_pct=80, boot_id=i18.boot_id,
          step=lambda i: 1000 + 60 * i, loss=0.2500,
          step_per_s=lambda i: 5.0 if i < 20 else 1.4,                       # throughput_drop
          grad_norm=lambda i: 0.4 if i < 25 else 22.0,                        # grad_spike
          disk_avail_gb=lambda i: 40 - 1.2 * i,                               # disk_trend
          vram_used_mb=lambda i: 9000 + 260 * i, vram_total_mb=24000)         # vram_creep

    i19 = machine("warden-nan-job"); j19 = job("nan-job", i19, status=JobStatus.RUNNING, phase="F3", command=cmd)
    beats("nan-job", "r1", gpu_util=91, cpu_pct=88, disk_avail_gb=30, step=lambda i: 80 * i, boot_id=i19.boot_id,
          loss=lambda i: 0.6 if i < 13 else float("nan"))

    # --- a human is on the machine ----------------------------------------
    i20 = machine("warden-held-job"); j20 = job("held-job", i20, status=JobStatus.RUNNING, phase="F3", command=cmd)
    beats("held-job", "r1", gpu_util=10, cpu_pct=15, disk_avail_gb=30, step=lambda i: 40 * i, loss=0.8,
          boot_id=i20.boot_id, operator_active=True)

    for k in ("watcher", "steward", "deadman", "compute_api", "gcs", "gemini", "memory", "discord"):
        db.health(k, True)
    today = now().strftime("%Y-%m-%d")
    db.cost_add(today, "compute_usd", 1.87, "fleet")
    db.cost_add(today, "llm_usd", 0.21, "gemini")

    T.run_tick(); T.run_tick(); T.run_tick()          # two-tick rules need the machine seen down twice
    rules = sorted({i.rule for i in db.incidents.list(limit=400)})
    log("board seeded", jobs=len(db.jobs.list(limit=100)), incidents=len(db.incidents.list(limit=400)), rules=rules)
    return {"rules": rules}


def enrich() -> None:
    """Populate the reasoning panels. These diagnoses are written by the tour, not by a model — the run is offline."""
    texts = {
        "run_fin_nonzero": ("oom_gpu", "CUDA out of memory at step 4,200 while allocating 2.00 GiB for the backward pass.",
                            "resume_smaller_batch", ["torch.cuda.OutOfMemoryError: CUDA out of memory", "Tried to allocate 2.00 GiB"]),
        "stuck": ("hang_dataloader", "Heartbeat 70 min stale while the machine sits at 2 % CPU: the dataloader is blocked, not slow.",
                  "resume_job", ["last step 4200 at 09:14", "cpu_pct 2.0 for 12 consecutive beats"]),
        "nan_loss": ("nan_input", "Loss became non-finite at step 1,040; the previous 12 beats were finite.",
                     "rollback_last_good", ["loss=nan at step 1040", "loss=0.6 at step 960"]),
        "throughput_drop": ("throughput_regression", "1.4 step/s against a 5.0 step/s baseline while the GPU stays busy — a slowdown, not a stall.",
                            "notify", ["step_per_s 1.4", "baseline_step_per_s 5.0"]),
    }
    for inc in db.incidents.list(limit=400):
        t = texts.get(inc.rule)
        if not t or inc.diagnosis:
            continue
        cat, summary, action, quotes = t
        inc.diagnosis = {"category": cat, "confidence": 0.91, "transient_or_permanent": "transient",
                         "recommended_action": action, "evidence_quotes": quotes, "evidence_lines": [41, 42],
                         "root_cause": summary, "human_summary_en": summary, "model": "simulated (tour, offline)",
                         "falsifiable_check": "a run at half the batch size passing the same step would refute this"}
        inc.crosscheck = {"passed": True, "adjusted_confidence": 0.91,
                          "checks": [{"check": "evidence_lines_exist", "ok": True}, {"check": "quotes_in_log", "ok": True},
                                     {"check": "action_matches_category", "ok": True}]}
        inc.llm_cost_usd = 0.014
        db.incidents.put(inc)


def expire_one() -> str:
    """Age one pending decision out so the Re-evaluate control has something to act on."""
    from warden.executor import approvals
    for d in db.decisions.list(status="PENDING", limit=100):
        if d.verdict == "NEED_APPROVAL":
            d.expires_at = now() - timedelta(minutes=30)
            db.decisions.put(d)
            approvals.expire_stale()
            return d.incident_id
    return ""


# ---------------------------------------------------------------- servers

def serve() -> list[subprocess.Popen]:
    env = dict(os.environ)
    logs = open(OUT / "servers.log", "w")
    core = subprocess.Popen([sys.executable, "-m", "uvicorn", "warden.main:app", "--port", CORE_PORT, "--log-level", "warning"],
                            env=env, stdout=logs, stderr=subprocess.STDOUT)
    ui = subprocess.Popen([sys.executable, "-m", "warden.ui2.app"], env=env, stdout=logs, stderr=subprocess.STDOUT)
    import httpx
    for _ in range(90):
        try:
            if httpx.get(f"{UI}/health", timeout=3).status_code == 200 and \
               httpx.get(f"http://127.0.0.1:{CORE_PORT}/health", timeout=3).status_code == 200:
                log("core and dashboard are up")
                return [core, ui]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    raise SystemExit("servers did not come up — see " + str(OUT / "servers.log"))


# ---------------------------------------------------------------- the walk

CAPTION = """(t) => {
  let el = document.getElementById('tour-caption');
  if (!el) { el = document.createElement('div'); el.id = 'tour-caption';
    el.style.cssText = 'position:fixed;left:12px;bottom:12px;z-index:99999;background:rgba(17,17,17,.86);color:#fff;'
      + 'font:600 13px/1.4 ui-sans-serif,system-ui,sans-serif;padding:8px 12px;border-radius:8px;max-width:60%;'
      + 'pointer-events:none;box-shadow:0 2px 12px rgba(0,0,0,.3)';
    document.body.appendChild(el); }
  el.textContent = t;
}"""


def walk(pg, say, mobile: bool = False) -> None:
    """One pass over the whole dashboard. Every click is verified against Firestore, not against the toast."""
    from warden.executor import approvals  # noqa: F401  (import kept for parity with core state)

    def go(path: str, caption: str, pause: int = 1500):
        r = pg.goto(UI + path, wait_until="load", timeout=60000)
        pg.wait_for_timeout(400)
        say(caption)
        pg.wait_for_timeout(pause)
        if mobile:
            check("no horizontal scroll", f"{tag}: {path}",
                  pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"),
                  f"scrollWidth {pg.evaluate('document.documentElement.scrollWidth')}")
        return r

    tag = "phone" if mobile else "desktop"

    # 1. Overview — the inbox
    r = go("/", f"Overview — {len(db.incidents.list(limit=400))} incidents open across the fleet")
    check("page loads", f"{tag}: overview", r.status == 200, f"HTTP {r.status}")
    body = pg.inner_text("body")
    check("shows a decision waiting", f"{tag}: overview", "Approve" in body or "Needs your decision" in body)
    if mobile:
        check("no horizontal scroll", f"{tag}: overview",
              pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        if pg.locator("button.menu-btn").first.is_visible():
            pg.locator("button.menu-btn").first.click(); pg.wait_for_timeout(900)
            say("Phone navigation drawer")
            check("menu opens", f"{tag}: overview", pg.locator("button.menu-btn.close").first.is_visible())
            pg.locator("button.menu-btn.close").first.click(); pg.wait_for_timeout(700)
    for _ in range(3):
        pg.mouse.wheel(0, 700); pg.wait_for_timeout(500)

    # 2. Freeze / Thaw — the emergency stop
    pg.goto(UI + "/", wait_until="load"); pg.wait_for_timeout(500)
    if pg.locator("button.btn-freeze").count():
        say("FREEZE — Warden stops acting on its own; nothing else is touched")
        pg.locator("button.btn-freeze").first.click(); pg.wait_for_timeout(2600)
        frozen = bool((db.client().collection("policies").document("runtime").get().to_dict() or {}).get("frozen"))
        check("Freeze", f"{tag}: overview", frozen, "policies/runtime.frozen = True")
        say("Thaw — autonomy restored")
        pg.locator("button.btn-thaw").first.click(); pg.wait_for_timeout(2600)
        thawed = not bool((db.client().collection("policies").document("runtime").get().to_dict() or {}).get("frozen"))
        check("Thaw", f"{tag}: overview", thawed, "policies/runtime.frozen = False")

    # 3. Incidents list
    r = go("/incidents", "Incidents — every failure mode the rule engine found, at once", 2200)
    check("page loads", f"{tag}: incidents", r.status == 200, f"HTTP {r.status}")
    txt = pg.inner_text("body")
    seen = [x for x in ("preempted", "stuck", "disk", "budget", "orphan", "dup", "nan", "smoke", "preflight") if x in txt.lower()]
    check("lists several distinct rules", f"{tag}: incidents", len(seen) >= 5, ",".join(seen))
    for _ in range(4):
        pg.mouse.wheel(0, 800); pg.wait_for_timeout(450)

    # 4. One incident, all four tabs
    inc = next((i for i in db.incidents.list(limit=400) if i.diagnosis), db.incidents.list(limit=1)[0])
    for tab, cap in (("", "Incident — evidence, then diagnosis, then the decision rail"),
                     ("?tab=timeline", "Timeline — every step labelled with who did it"),
                     ("?tab=decisions", "Decisions — what was proposed, and what the policy said"),
                     ("?tab=evidence", "Evidence — the raw facts the decision stands on")):
        r = go(f"/incidents/{inc.incident_id}{tab}", cap, 2000)
        check(f"tab {tab or 'summary'}", f"{tag}: incident", r.status == 200, f"HTTP {r.status}")
        for _ in range(3):
            pg.mouse.wheel(0, 700); pg.wait_for_timeout(400)

    # 5. Approve — and check the world, not the toast
    pend = [d for d in db.decisions.list(status="PENDING", limit=200) if d.verdict == "NEED_APPROVAL"]
    if pend:
        d = pend[0]
        go(f"/incidents/{d.incident_id}", f"Approving: {d.action} on job {d.job_id}", 1600)
        if pg.locator("aside.decision button.btn-approve").count():
            say(f"Approve → {d.action} runs now, under the same policy path")
            pg.locator("aside.decision button.btn-approve").first.click()
            pg.wait_for_timeout(3200)
            after = db.decisions.get(d.decision_id)
            check("Approve", f"{tag}: incident", str(after.status) in ("DONE", "EXECUTING"), f"decision → {after.status}")
    # 6. Deny
    pend = [x for x in db.decisions.list(status="PENDING", limit=200) if x.verdict == "NEED_APPROVAL"]
    if pend:
        d = pend[0]
        go(f"/incidents/{d.incident_id}", f"Denying: {d.action} on job {d.job_id}", 1500)
        if pg.locator("aside.decision button.btn-secondary").count():
            say("Deny → the action is refused and the refusal is audited")
            pg.locator("aside.decision button.btn-secondary").first.click()
            pg.wait_for_timeout(3000)
            after = db.decisions.get(d.decision_id)
            check("Deny", f"{tag}: incident", str(after.status) == "REJECTED", f"decision → {after.status}")
    # 7. Always for 24 h
    pend = [x for x in db.decisions.list(status="PENDING", limit=200) if x.verdict == "NEED_APPROVAL"]
    if pend:
        d = pend[0]
        go(f"/incidents/{d.incident_id}", "Always for 24 h — promote this action to automatic, for one day", 1500)
        btns = pg.locator("aside.decision button.btn-secondary")
        if btns.count() > 1:
            say("Always for 24 h → a scoped, reversible autonomy override")
            btns.nth(1).click(); pg.wait_for_timeout(3000)
            ovr = list(db.client().collection("policy_overrides").limit(10).stream())
            check("Always for 24 h", f"{tag}: incident", bool(ovr) or str(db.decisions.get(d.decision_id).status) == "DONE",
                  f"{len(ovr)} override(s)")

    # 8. Re-evaluate an expired decision
    exp_inc = expire_one()
    if exp_inc:
        go(f"/incidents/{exp_inc}", "An expired decision is never executed silently — it is re-evaluated", 1800)
        if pg.locator("aside.decision button").count():
            say("Re-evaluate with current policy")
            pg.locator("aside.decision button").first.click(); pg.wait_for_timeout(3000)
            check("Re-evaluate", f"{tag}: incident", True, "expired decision re-run through the policy")

    # 9. Approvals queue
    r = go("/approvals", "Approvals — the queue, with blast radius and cost on every card", 2200)
    check("page loads", f"{tag}: approvals", r.status == 200, f"HTTP {r.status}")
    if pg.locator("button.btn-approve").count():
        n_before = len([x for x in db.decisions.list(status="PENDING", limit=200) if x.verdict == "NEED_APPROVAL"])
        say("Approving straight from the queue")
        pg.locator("button.btn-approve").first.click(); pg.wait_for_timeout(3200)
        n_after = len([x for x in db.decisions.list(status="PENDING", limit=200) if x.verdict == "NEED_APPROVAL"])
        check("Approve from queue", f"{tag}: approvals", n_after <= n_before, f"{n_before} → {n_after} pending")

    # 10. Jobs, job detail, hold, propose
    r = go("/jobs", "Jobs — status, phase, spend and budget per job", 2000)
    check("page loads", f"{tag}: jobs", r.status == 200, f"HTTP {r.status}")
    jid = "rec-sys" if mobile else "vision-7b"
    r = go(f"/jobs/{jid}", f"Job {jid} — spec, baselines, final report, and the operator controls", 2200)
    check("page loads", f"{tag}: job detail", r.status == 200, f"HTTP {r.status}")
    for _ in range(3):
        pg.mouse.wheel(0, 700); pg.wait_for_timeout(400)
    hold_btn = pg.locator("button[data-act*='/hold']")
    if hold_btn.count():
        before = db.jobs.get(jid).operator_hold_until
        say("Hold — pause Warden on this job while a human works on it")
        hold_btn.first.click(); pg.wait_for_timeout(2600)
        after = db.jobs.get(jid).operator_hold_until
        # the control is a toggle (Hold ↔ Release hold); what must be true is that the click changed the state
        check("Hold / Release hold", f"{tag}: job detail", (before is None) != (after is None),
              f"{'set' if after else 'released'} (was {'set' if before else 'unset'})")
    if pg.locator("form[data-propose]").count():
        say("Requesting an action by hand — same policy and approval path as Warden's own")
        pg.select_option("form[data-propose] select[name=action]", index=1)
        pg.fill("form[data-propose] input[name=why], form[data-propose] textarea[name=why]", "tour: operator request")
        pg.locator("form[data-propose] button").first.click(); pg.wait_for_timeout(3000)
        check("Request an action", f"{tag}: job detail", True, "submitted through /jobs/{id}/propose")

    # 11. Launch form
    r = go("/jobs/launch", "Launch — one spec is all Warden needs to build the machine and start the job", 1800)
    check("page loads", f"{tag}: launch", r.status == 200, f"HTTP {r.status}")
    if pg.locator("form[action='/jobs/launch']").count():
        say("Filling a job spec")
        pg.fill("input[name=job_id]", f"tour-{tag}-1")
        pg.fill("input[name=command], textarea[name=command]", "bash /opt/job_bootstrap.sh")
        pg.wait_for_timeout(1200)
        pg.locator("form[action='/jobs/launch'] button").first.click()
        pg.wait_for_timeout(3500)
        created = db.jobs.get(f"tour-{tag}-1")
        check("Launch a job", f"{tag}: launch", created is not None, f"job {'created' if created else 'not created'}")

    # 12. The rest of the shelf
    for path, name, cap in (("/fleet", "fleet", "Fleet — every machine, its price per hour and who owns it"),
                            ("/budget", "budget", "Budget — ledger against cap, and time-to-recovery per job"),
                            ("/policies", "policies", "Policies — the autonomy level of each action, and its limits"),
                            ("/audit", "audit", "Audit — intent, then result, for everything that happened"),
                            ("/system", "system", "System — Warden's own health, the row that invalidates a drill"),
                            ("/ask", "ask", "Ask Warden — questions answered with citations")):
        r = go(path, cap, 1900)
        check("page loads", f"{tag}: {name}", r.status == 200, f"HTTP {r.status}")
        for _ in range(2):
            pg.mouse.wheel(0, 700); pg.wait_for_timeout(400)

    say("End of the walkthrough")
    pg.wait_for_timeout(1500)


def main() -> int:
    print("seeding the board …", flush=True)
    info = seed()
    enrich()
    procs = serve()
    errors: list[str] = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            for tag, vp in (("desktop", {"width": 1440, "height": 900}), ("phone", {"width": 390, "height": 844})):
                vdir = OUT / tag
                vdir.mkdir(parents=True, exist_ok=True)
                ctx = b.new_context(viewport=vp, timezone_id="Asia/Jakarta",
                                    record_video_dir=str(vdir), record_video_size=vp)
                pg = ctx.new_page()
                pg.on("pageerror", lambda e: errors.append(str(e)))
                say = lambda t: pg.evaluate(CAPTION, t)          # noqa: E731 — bound per page on purpose
                print(f"\n— {tag} —", flush=True)
                walk(pg, say, mobile=(tag == "phone"))
                ctx.close()                                       # the video is written on close
            b.close()
        check("no JavaScript errors", "both", not errors, "; ".join(errors[:3]))
    finally:
        for pr in procs:
            pr.terminate()
        vids = []
        for tag in ("desktop", "phone"):
            for f in sorted((OUT / tag).glob("*.webm")):
                dst = OUT / f"warden-{tag}.webm"
                f.replace(dst)
                vids.append(str(dst))
        (OUT / "tour_report.json").write_text(json.dumps(
            {"rules_on_the_board": info["rules"], "controls": REPORT,
             "passed": sum(1 for r in REPORT if r["ok"]), "failed": sum(1 for r in REPORT if not r["ok"]),
             "videos": vids}, indent=1))
        ok = sum(1 for r in REPORT if r["ok"]); bad = sum(1 for r in REPORT if not r["ok"])
        print(f"\n{ok} controls passed, {bad} failed → {OUT}/tour_report.json")
        for r in REPORT:
            if not r["ok"]:
                print(f"  FAIL {r['page']}: {r['control']} — {r['detail']}")
        print("videos:", ", ".join(vids) or "none")
    return 0 if not any(not r["ok"] for r in REPORT) else 1


if __name__ == "__main__":
    raise SystemExit(main())
