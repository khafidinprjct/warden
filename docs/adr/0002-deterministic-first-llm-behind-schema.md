# ADR-0002 · Deterministic first; the LLM never holds the button
**Status:** accepted · **Phase:** 1, 4

## Context
A wrong OOM diagnosis once led to a patch on the wrong process and a burned GPU budget (catalog #10). Status, mtime, size, exit code, checksum and process count are facts; a language model is only useful for text that regexes cannot classify safely.

## Decision
Rules run first and decide alone whenever they can (preempt, idle, orphan, invalid marker, disk). The LLM is invoked only from the incident pipeline, through Google ADK with a fixed `output_schema` (`Diagnosis`: category from an enum, confidence, cited `evidence_lines`, `evidence_quotes`, transient/permanent, `recommended_action` from an enum, `needs_human`, `falsifiable_check`). A deterministic cross-check then verifies that cited lines exist, quotes are substrings, and category-specific numbers agree; confidence is adjusted downward on failure. Execution is driven by the policy engine and executor, never by the model's text. A second model (3.7 Flash) is consulted when confidence < 0.7; disagreement escalates to a human.

## Consequences
+ Every LLM claim is falsifiable and auditable; a wrong diagnosis costs a cheap API call, not a machine. − Some diagnoses that a human would make instantly wait for approval; cost ≈ $0.01–0.03 per incident.

## Evidence
`warden/agents/{schemas,diagnostician,crosscheck,pipeline}.py`; smoke test on a real NaN log; `tests/test_infra_chaos.py` (Gemini unavailable → circuit breaker → escalation).
