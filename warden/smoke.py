"""make smoke — memuat komponen ASLI: Firestore emulator, fake GCE, aturan, kebijakan, executor, dan
satu panggilan Gemini 3.5 asli (Diagnostician + cek silang) pada log nyata. Gagal = exit 1 (nyaring)."""
from __future__ import annotations

import sys
from pathlib import Path

from warden.core.models import Heartbeat, IncidentState, Job, JobStatus, now
from warden.providers.registry import compute
from warden.store import firestore as db
from warden.watcher.tick import run_tick


def main() -> int:
    fake = compute()
    assert type(fake).__name__ == "FakeGCE", "smoke butuh WARDEN_PROVIDER=fake"
    # skenario A: preempt spot → start otomatis
    inst = fake.add("demo-train-1")
    job = Job(job_id="demo", name="demo", instance_ref=inst.ref, status=JobStatus.RUNNING, run_id="r1", phase="F3",
              command="/venv/bin/python run_pipeline.py")
    db.jobs.put(job)
    from datetime import timedelta
    for i in range(10):
        db.put_heartbeat(Heartbeat(job_id="demo", run_id="r1", ts=now() - timedelta(minutes=10 - i), boot_id=inst.boot_id, phase="F3",
                                   step=i * 50, loss=0.5, gpu_util=90, cpu_pct=80, disk_avail_gb=40))
    s0 = run_tick(); assert s0["findings"] == 0, f"job sehat harus diam: {s0}"
    fake.preempt(inst.ref)
    s1 = run_tick(); assert s1["findings"] == 0, "tick pertama TERMINATED belum boleh alarm (dua tick)"
    s2 = run_tick(); assert s2["auto"] == 1, f"preempt harus start otomatis: {s2}"
    assert fake.describe(inst.ref).status == "RUNNING", "mesin harus RUNNING lagi"
    incs = db.incidents.list(rule="preempted")
    assert incs and incs[0].state == IncidentState.RESOLVED, f"insiden harus RESOLVED: {[i.state for i in incs]}"
    print("A preempt→start otomatis: OK", s2)
    # skenario B: marker DONE tanpa exit code ditolak
    from warden.core.models import Marker
    db.put_marker(Marker(job_id="demo", run_id="r1", kind="DONE_LEGACY"))
    s3 = run_tick(); assert any(i.rule == "done_without_exit" for i in db.incidents.list(job_id="demo")), "DONE tanpa exit harus ditolak"
    print("B DONE tanpa exit ditolak: OK")
    # skenario C: Gemini asli — diagnosis log NaN nyata + cek silang
    from warden.agents.crosscheck import crosscheck
    from warden.agents.diagnostician import diagnose
    log = Path(__file__).resolve().parent.parent / "tests/fixtures/golden_logs/run_eks_gagal1.log"
    lines = log.read_text(errors="ignore").splitlines()[-200:]
    diag, usage = diagnose({"job": "climate-eks", "hourly_usd": 0.384, "phase": "F3-4"},
                           [{"rule": "run_fin_nonzero", "exit_code": 1}], {"loss": None}, lines)
    cc = crosscheck(diag, lines, None)
    print(f"C Gemini {usage['model']}: category={diag.category} conf={diag.confidence:.2f} lines={diag.evidence_lines[:5]} "
          f"crosscheck={'LOLOS' if cc['passed'] else 'GAGAL'} biaya=${usage['cost_usd']:.4f}")
    assert diag.category in ("nan_input", "data_error", "code_bug", "nan_divergence"), f"kategori tak masuk akal: {diag.category}"
    assert cc["passed"], f"cek silang gagal: {cc['checks']}"
    db.cost_add(now().strftime("%Y-%m-%d"), "llm_usd", usage["cost_usd"], "smoke")
    print("SMOKE LULUS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
