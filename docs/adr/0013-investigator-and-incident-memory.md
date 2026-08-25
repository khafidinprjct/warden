# ADR-0013 · An Investigator agent and incident memory, in front of the Diagnostician
**Status:** accepted (25 Aug 2026) · **Phase:** A (post-plan)

## Context
The Diagnostician received a fixed bundle (job card, heartbeat summary, 200 log lines) and answered once. On a real incident (25 Aug, `run_fin_nonzero`) it could only say "unknown, confidence 0" because the decisive evidence was elsewhere (postmortem of the previous incident, artifact sizes, an earlier log window). Judges also ask for "context retrieval based on past interactions" and "adaptive, learning systems".

## Decision
1. **Investigator** — an ADK `LlmAgent` (Gemini 3.5 Flash) with six **read-only** tools: `get_log_window`, `search_log`, `get_heartbeats`, `get_artifacts`, `get_incident_history`, `get_instance`. It chooses its own evidence, bounded by a hard budget of 4 tool calls enforced in code (not only in the prompt), and writes an investigation note (hypotheses ranked, evidence with citations, ruled out, recommended check). The Diagnostician receives the note as an extra section and must still cite the log tail; the cross-check is unchanged.
2. **Incident memory** — every incident that reaches a terminal state gets a deterministic ten-line postmortem (symptom, evidence, diagnosis, actions, outcome, cost, lesson), embedded with Vertex AI `gemini-embedding-001` (768 d) and stored in Firestore with a vector index; recall by nearest neighbour (`find_nearest`, cosine) or by job/rule when no embedding is available. Written by the steward sweep; no LLM cost to write.
3. **Concierge** — the same tools and memory behind an operator question (`/ask` in the dashboard; Discord later). It never acts.
4. **Per-run logs** — the harness now keeps `log/<run_id>.log` next to `tail.log`, so a failed run's log survives the next run.

## Consequences
+ Diagnoses cite evidence the fixed bundle did not contain; repeat incidents are recognised. + Cost per incident ≈ $0.03–0.05 with the full model (the owner rejected a smaller model for reasoning). − One more LLM call per LLM-routed incident; tool results add prompt tokens (mitigated by truncation).

## Evidence
`warden/agents/{investigator,memory,concierge}.py`; real-data run 25 Aug: 12 postmortems written, vector recall returned the related incidents, Investigator hypothesis #1 correct (0-byte checkpoints) with citations, Concierge answer matched the audit trail; `tests/test_investigator_memory.py`.
