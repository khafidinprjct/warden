from datetime import timedelta
from warden.core.models import Heartbeat, Instance, InstanceStatus, Job, JobStatus, Marker, now
from warden.watcher.rules import Facts, evaluate
from warden.policy.engine import load_policy

P = load_policy()


def base(**kw):
    t = now()
    inst = Instance(ref="us-central1-a/vm1", name="vm1", zone="us-central1-a", status=InstanceStatus.RUNNING,
                    labels={"warden-managed": "true"}, managed=True, boot_disk_auto_delete=False, termination_action="STOP",
                    boot_id="b1", hourly_price_usd=0.03)
    job = Job(job_id="j1", status=JobStatus.RUNNING, phase="F3", run_id="r1", command="/venv/bin/python train.py")
    hbs = [Heartbeat(job_id="j1", run_id="r1", ts=t - timedelta(minutes=30 - i), phase="F3", step=i * 50, gpu_util=90, cpu_pct=80) for i in range(30)]
    f = Facts(t=t, inst=inst, job=job, hb=hbs[-1], hbs=hbs, policy=P, boot_age_min=60)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def rules(f):
    return {x.rule for x in evaluate(f)}


def test_healthy_is_silent():
    assert rules(base()) == set()


def test_preempt_two_ticks_no_fin():
    f = base()
    f.inst.status = InstanceStatus.TERMINATED; f.prev_status = InstanceStatus.TERMINATED
    f.preempt_events = [{"type": "compute.instances.preempted"}]
    assert "preempted" in rules(f)


def test_terminated_one_tick_is_not_yet_alarm():
    f = base(); f.inst.status = InstanceStatus.TERMINATED; f.prev_status = InstanceStatus.RUNNING
    assert "preempted" not in rules(f)


def test_stuck_requires_two_conditions():
    f = base(); f.hb = Heartbeat(job_id="j1", run_id="r1", ts=f.t - timedelta(minutes=40), phase="F3", gpu_util=95, cpu_pct=90)
    assert "stuck" not in rules(f) and "slow" in rules(f)      # basi tapi sibuk = lambat
    f.hb.gpu_util = 1; f.hb.cpu_pct = 2
    assert "stuck" in rules(f)


def test_done_without_exit_rejected():
    f = base(); f.done_legacy = Marker(job_id="j1", run_id="r1", kind="DONE_LEGACY")
    assert "done_without_exit" in rules(f)


def test_fin_nonzero_needs_llm():
    f = base(); f.run_fin = Marker(job_id="j1", run_id="r1", kind="RUN_FIN", exit_code=1, valid=True)
    fs = [x for x in evaluate(f) if x.rule == "run_fin_nonzero"]
    assert fs and fs[0].needs_llm


def test_unsafe_config():
    f = base(); f.inst.boot_disk_auto_delete = True
    assert "unsafe_config" in rules(f)


def test_orphan_needs_quiet_and_grace():
    f = base(); f.job = None; f.in_ledger = False; f.hb = None
    assert "orphan" in rules(f)
    f.boot_age_min = 3
    assert "orphan" not in rules(f)


def test_disk_low():
    f = base(); f.hb = Heartbeat(job_id="j1", run_id="r1", ts=f.t, phase="F3", gpu_util=90, cpu_pct=80, disk_avail_gb=2.0)
    assert "disk_low" in rules(f)


def test_nan_loss():
    f = base(); f.hb = Heartbeat(job_id="j1", run_id="r1", ts=f.t, phase="F3", gpu_util=90, cpu_pct=80, loss=float("nan"))
    assert "nan_loss" in rules(f)
