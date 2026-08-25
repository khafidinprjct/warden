# Warden runbook (operator)

## Daily
- Digest (08:00 WIB): spend, ETTR, incidents, autonomy changes. No digest → check `/system` (watcher/steward stale?) and Cloud Scheduler.
- Dashboard → Approvals: decide what is `Awaiting approval` (expires in 30 min → escalated). `Escalated` → read Recovery (what Warden tried, why it stopped), act, then close or mark false positive.

## Launch a job
Dashboard → Jobs → **Launch job**, or `python -m warden.cli launch spec.yaml`, or `POST /jobs/launch` (HMAC). Spec: `job_id`, `command` (runs under `wrun` on the machine), `machine_type`, `zones` (in order), `spot`, `disk_gb`, `expect` (artifacts and row counts), `budget_cap_usd`, `entry`, `env`.
Warden: ledger → zone (stock-outs, quota) → VM → first heartbeat = RUNNING → guards → artifacts verified → report → machine stopped.

## When something happens
| Symptom | What Warden does | What you do |
|---|---|---|
| `Instance preempted` | start → verify new boot + progress → else relocate (approval) | approve relocation, or hold the job |
| `Run exited with error` | investigate → diagnose → ladder (e.g. OOM: batch 0.5 → 0.25 → bigger machine) → verify each step | nothing, unless a rung needs approval or the ladder is exhausted |
| `Non-finite loss` / `Gradient spike` | rollback to last good checkpoint with lower lr (approval) | approve; after 5 approvals without failure it becomes automatic for that job |
| `Disk filling up` / `Disk space low` | clean local checkpoints already in Storage → resize disk (approval) | approve resize if cleaning freed nothing |
| `Job stuck` | kill + resume → clean restart | — |
| `Artifact verification failed` | quarantine on the machine, job stays FINISHED_UNVERIFIED | choose rerun (`Request an action → Resume job`) or rollback |
| `Preflight failed` / `Smoke test incomplete` | machine stopped | fix the image/dependencies, relaunch |
| `Budget 80 %` / `Budget exhausted` | warn / stop the machine | raise `budget_cap_usd` on the job and resume, or accept |
| Wrong alarm | — | **Mark as false positive**; after two in 7 days Warden withholds that action for that job |
| Warden did something you did not want | — | **Freeze** (red button) → then `Hold` the job; demote the action in `policies/<job_id>` or `policies.yaml` |
| Warden itself silent | deadman stops managed machines after 15 min without a core heartbeat | Cloud Run `warden-core` logs; Scheduler `warden-tick`; `/system` |

## Operator requests
Job page → **Request an action** (any action; same policy/approval path as Warden's own proposals) · **Hold 2 h** (manual mode, Warden observes only) · Ask Warden for evidence-cited answers.
CLI: `python -m warden.cli launch|job list|job show|tick|steward|freeze on|off|approve|deny <id>|ettr <job>`.

## Evaluation
`python -m warden.eval.gold` (≈ $0.07) runs the gold set (real logs) against the Diagnostician; nightly at 02:00 WIB via `/eval`; below 0.9 → health `gold_eval` red + notification. Run it after every prompt or model change.

## Recovery of Warden itself
State is in Firestore: incidents keep their verification spec, attempt count and remaining hypotheses, so a restart of warden-core resumes where it stopped. Re-deploy: `gcloud run deploy warden-core --source .` (and `warden-ui`). Secrets: `warden-ingest-hmac` (rotate with `infra/rotate_hmac.py`, grace period keeps old signatures valid).
