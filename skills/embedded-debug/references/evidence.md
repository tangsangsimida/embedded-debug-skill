# Diagnostic evidence contract

The agent may use the CLI's standard JSON envelope as the raw source, but the
final diagnosis must normalize important facts into an auditable record. Keep
raw command output and paths to logs when available; do not replace evidence
with a prose summary.

## Evidence record

Each operation record should contain:

```json
{
  "step": "read-variable",
  "operation": "edbg variable read edbg_sample_ticks",
  "task_id": "diagnostic-task-123",
  "session": "bench-a",
  "source": "cli-json|gdb-mi|openocd|source|build|test|user",
  "observed_at": "RFC-3339 timestamp",
  "success": true,
  "target_state_before": "halted|running|unknown|disconnected",
  "target_state_after": "halted|running|unknown|disconnected",
  "authorization": "none|run|breakpoint|expression|write|flash|reset",
  "authorization_source": "user-confirmed|cli-flag|policy|unknown",
  "observations": [],
  "diagnostics": [],
  "raw_artifact": "relative path or null"
}
```

`observations` are facts, not conclusions. A failed operation is still useful
evidence and must retain its stable error code, message, and diagnostics.
`task_id` and `step_id` can be supplied through `EDBG_TASK_ID` and
`EDBG_STEP_ID`; otherwise the CLI command is used as the step. The CLI records
the current session name and leaves `authorization_source` as `unknown` unless
the caller explicitly supplies a provenance value. These fields add context;
they do not turn a CLI flag into proof of user approval.

## Confidence and claim levels

Use one of these labels for conclusions:

- `observed`: directly returned or reproduced;
- `confirmed`: supported by two independent observations or one decisive,
  reproducible test;
- `inferred`: plausible explanation that still needs a confirming check;
- `unknown`: not established;
- `rejected`: contradicted by recorded evidence.

A report may call a root cause confirmed only when it states the observations
that connect the cause to the symptom and names the verification that would
fail if the cause were wrong. Do not upgrade a static suspicion to confirmed.

## Minimum final report

```text
Problem:
Project/target/tool versions:
Discovery and selected configuration:
Hypotheses and status:
Key operations and evidence:
Root cause and confidence:
Source changes:
Build and software regression:
Hardware regression and final target state:
Unverified items / remaining risk:
Required human action:
```

Use `not run` when a validation stage was not attempted and `blocked` when it
could not run. Never use a green mock or CI result as proof of physical target
behavior. Hardware records must include the exact repository commit, ELF
identity, tool versions, session cleanup result, and whether target-affecting
operations were authorized.
