# ADR-0004 · STOP, never DELETE
**Status:** accepted · **Phase:** 0, 3

## Context
75 clips were lost when a Spot machine was terminated with an auto-deleting boot disk (catalog #2).

## Decision
Delete is not an action in the `Action` enum, not a handler in the executor, not in the custom IAM role `wardenInstanceOperator`, and it is in the policy's `hard_deny` list — four independent layers. Instances are created with `--instance-termination-action=STOP` and `--no-boot-disk-auto-delete`; the Watcher raises `unsafe_config` for anything else.

## Consequences
+ No automation path can destroy data. − Stopped machines still cost disk storage; cleanup is a deliberate human act in the Console.

## Evidence
`warden/core/models.py::Action`, `warden/policy/policies.yaml::hard_deny`, `infra/vm_create.sh`, `tests/test_policy.py`.
