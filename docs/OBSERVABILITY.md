# Observability (Phase 13) — 25 Aug 2026

## Structured events (warden-core stdout → Cloud Logging `jsonPayload`)
| event | fields | emitted by |
|---|---|---|
| `warden.heartbeat` | tick_ms, instances, findings, auto, approval | every Watcher tick (`watcher/tick.py`) |
| `warden.incident` | phase=opened, rule, sev, job, incident_id, detect_ms (instance stop → incident, when the stop time is known) | `_handle` |
| `warden.decision` | action, verdict, autonomy, incident_id, decision_ms (incident opened → decision) | `_handle` |
| `warden.llm` | ok, model, ms, cost_usd, incident_id, category · on failure: ok=false, error | `agents/pipeline.py` |

## Log-based metrics (`logging.googleapis.com/user/…`)
`warden_heartbeat` (count) · `warden_tick_ms` (dist) · `warden_incidents_opened` (count; labels rule, sev) · `warden_detect_ms` (dist) · `warden_decision_ms` (dist) · `warden_llm_calls` (count; labels ok, model) · `warden_llm_cost_usd` (dist) · `warden_llm_ms` (dist)

## Dashboard
Cloud Monitoring → Dashboards → **Warden — operations** (`projects/603873318528/dashboards/ea6cf52d-d615-41a9-b0ff-3458c5936f94`): tick p50/p95, incidents per hour by rule, detection p95 vs 60 s, decision p95 vs 30 s, watcher heartbeats per 5 min, Gemini ok/failed, Gemini cost per call, dead-letter count, Cloud Run request latency per service.

## SLOs (custom service `warden-core`, rolling 7 days)
| SLO | SLI | goal |
|---|---|---|
| Decision within 30 s of detection | `warden_decision_ms` ≤ 30 000 (distribution cut) | 99 % |
| Detection within 60 s of instance stop | `warden_detect_ms` ≤ 60 000 | 90 % — requires the 1-minute tick (`warden-tick` schedule changed from */2 to * * * * * on 25 Aug) |
| Watcher available | ≥ 1 `warden_heartbeat` in every 5-minute window | 99 % |

## Alerts (email channel 7359472618699335008)
`warden_heartbeat absent` (core silent 10 min, per service) · `warden tick latency p95 > 30 s` · `warden Gemini failures` (> 2 failed calls / 5 min) · `warden dead-letter messages` · Billing budget 25/50/80/100 %.

## Known limits
Detection latency is only measured when the provider reports the instance stop time (`last_stop_at`); preemptions detected by heartbeat loss alone have no `detect_ms`. SLO history starts 25 Aug 2026, 22:30 WIB; the 7-day gate of Phase 14 is reachable on 1 Sep.
