# Autonomous Debugging Workflow

This workflow is the agent's default loop for a natural-language firmware bug. It
is a reasoning contract, not a fixed GDB script. The agent chooses the smallest
set of operations that can distinguish the current hypotheses and records the
result of every meaningful step. See [evidence](evidence.md) for the evidence
contract and [safety](safety.md) for authorization boundaries.

## 1. Understand

Translate the report into a short problem record before touching the target:

- symptom, expected behavior, observed behavior, and reproduction conditions;
- timing/frequency and whether the failure is deterministic;
- known architecture, board, peripherals, interrupts, DMA, and RTOS context;
- attempted fixes and existing logs.

Missing information is not a reason to stop. Mark it as unknown, inspect the
workspace first, and ask only for a condition that discovery cannot establish
and that would change the safe diagnostic choice.

## 2. Discover

Run project and environment discovery before inventing configuration. Reuse IDE
launch/tasks, Make/CMake/PlatformIO commands, existing ELF/DWARF, OpenOCD
arguments, and an already-running local GDB server. Confirm the selected ELF,
GDB, OpenOCD, session name, and target endpoint in the evidence record.

Do not infer an MCU model from a probe name, maintain a chip allowlist, or ask
the developer to recreate a configuration already present in the project. A
capability remains `unknown` until the backend response proves it.

## 3. Plan

Maintain a live hypothesis table. Each row contains:

| Field | Required content |
| --- | --- |
| hypothesis | A falsifiable cause, not a restatement of the symptom |
| evidence_needed | The smallest data set that can distinguish it |
| operation | Existing CLI operation or local/static analysis step |
| preconditions | ELF, halted state, session, authorization, or tool requirement |
| side_effect | Target or host impact, including stop/resume effects |
| pass/fail rule | What observation supports or rejects the hypothesis |
| fallback | A bounded next method if the operation is unavailable |

Order rows by information value and risk. Do not run every debugger command by
default. Re-plan after each result; retire hypotheses only when the evidence
actually contradicts them.

## 4. Execute

Use the existing `edbg` JSON interface. For an auditable run, set
`EDBG_EVIDENCE_FILE` to a user-selected JSONL path before invoking the CLI and
retain the raw GDB/OpenOCD logs separately. Keep one named session for a target and
check its status before every halted-state-dependent operation. A read of
registers, memory, variables, or a stack is valid only after the response proves
`target_state=halted`; a command result of `^done` alone is not proof of a halt.

Start with read-only evidence. Run control, breakpoints, reset, memory/register
writes, arbitrary expressions, flash, and firmware that may actuate hardware
require the corresponding explicit authorization immediately before execution.
Never convert a broad earlier approval into silent authorization for a new
operation or target.

## 5. Reason and verify

For every result, separate:

1. **Observed**: directly returned by the CLI, debugger, source, build, or test.
2. **Inference**: a reasoning step that connects observations to a hypothesis.
3. **Unknown**: a condition not established by available evidence.
4. **Next check**: the operation that could confirm or reject the inference.

Do not call a suspicious line the root cause until a runtime observation,
reproducible test, or a code-level proof connects it to the symptom. A single PC
sample is insufficient to distinguish a loop, a lock, normal waiting, or a
stopped fault. If evidence is contradictory, preserve both results and re-plan.

## 6. Fix and regress

Only modify source after the developer authorizes the workspace change. Make the
smallest change that addresses the confirmed mechanism, then inspect its impact
on concurrency, interrupts, timing, and hardware outputs. Code authorization
does not authorize flash, reset, halt, continue, or actuator operation.

Re-run the existing build and software tests first. If hardware authorization
and a safe fixture are available, run a separately recorded hardware regression.
Report compile success, software-test success, and real-target behavior as three
different claims. If hardware validation is unavailable, say so explicitly.

## 7. Report and clean up

Produce a concise engineering report using the fields in [evidence](evidence.md):
problem, environment, hypotheses, operations, observations, rejected causes,
root cause and confidence, source changes, build/tests, hardware result, final
target state, unresolved risks, and any required human action.

Always attempt bounded cleanup in a `finally`-equivalent path. Stop only
processes owned by this session, remove breakpoints created by the workflow,
restore the requested safe target state, and record cleanup failures without
replacing the primary diagnostic failure.

## Failure recovery limits

For missing tools, invalid ELF/configuration, endpoint conflicts, permissions,
timeouts, or disconnected sessions, preserve the exact error and diagnostics.
Use the existing discovery/environment/session recovery paths first. Retry the
same failing operation at most once unless new evidence changes the inputs;
then choose a different diagnostic method or report the blocker. Never loop
indefinitely or silently reinstall a working system toolchain.
