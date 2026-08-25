# ADR-0007 · Verification means opening the artifact
**Status:** accepted · **Phase:** 5

## Context
A corrupt checkpoint had exactly the size of a healthy one; a CSV was truncated while `RUN_FIN` said exit 0 (catalog #7, #21).

## Decision
A job becomes `COMPLETE` only after the verifier fetches every artifact declared in `RUN_FIN` and opens it with a type-specific plugin (csv rows/columns/NaN/range/ID alignment, json keys, jsonl trailing newline, npz keys and finiteness, parquet footer, torch zip integrity), checks the declared sha256, compares size to learned expectations, and refuses to measure while a writer is active. Missing artifacts within 10 minutes of `RUN_FIN` are retried (upload grace); broken artifacts are quarantined (renamed, never deleted); the job stays `FINISHED_UNVERIFIED` and a human is asked.

## Consequences
+ "Done" cannot be faked by a marker. − Verification is bounded by artifact size (200 MB upload cap; larger checkpoints verify on the machine via the mailbox — planned).

## Evidence
`warden/verifier/`, `tests/test_verifier.py`, `tests/test_verify_flow_fake.py`, chaos #21.
