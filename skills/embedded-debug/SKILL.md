---
name: embedded-debug
description: Debug embedded firmware through user-configured OpenOCD and GDB. Use for checking toolchains, connecting to MCUs, reading registers or variables, inspecting memory and stacks, managing breakpoints, diagnosing faults, and safely flashing firmware.
license: MIT
compatibility: Requires an agent that can run terminal commands; Python 3.11+ for the CLI and user-installed GDB/OpenOCD for hardware access.
metadata:
  version: "0.1.0"
---

# Embedded debugging workflow

1. Identify the active project and requested operation. Run `edbg discover --json` to inspect VS Code launch/tasks, existing build files, ELF candidates, and active local GDB servers. Never infer a target from a vendor/model name.
2. Run `edbg doctor --json` and `edbg config validate --json`. If `edbg` is missing, run the bundled `scripts/run_edbg.py` from the installed Skill directory to check the pinned CLI version. It never installs by default. Ask the user before invoking it with `--install-python-cli`; it installs only the pinned Python package and does not alter system toolchains.
3. Reuse the project's build/flash command, IDE launch settings, OpenOCD scripts, ELF, and an already-running GDB server when their source is unambiguous. Do not require `.edbg.toml` or a standalone target.cfg: a board/full config, VS Code launch settings, or existing command-line arguments may already define the target. `edbg connect` reuses an open local GDB Server automatically, then verifies the GDB connection and target response. Use a matching GDB and start with read-only operations.
   If the user is debugging multiple boards/targets, assign a distinct session name for each (for example `edbg --session bench-a connect ...`) and reuse that selector for later commands. Check `edbg session list` before starting another session; do not claim a GDB endpoint already owned by a different session.
4. Before flash/erase, reset, memory/register writes, or running firmware that may actuate hardware, obtain explicit user authorization for that exact action. Never disable safety interlocks. Use `--authorize` for guarded CLI writes and record changes.
5. Report observed facts separately from hypotheses. An unknown capability is not supported. A stopped GDB read is not non-intrusive telemetry.
6. Ask for more information only when discovery finds no usable existing configuration or finds multiple ambiguous candidates. On failure, preserve the exact tool error and diagnostics; do not substitute chip-specific assumptions. Stop only processes owned by this CLI and restore the target to the requested safe state.

See [setup](references/setup.md), [debugging](references/debugging.md), [fault diagnosis](references/hardfault.md), [ThreadX](references/threadx.md), and [safety](references/safety.md).
