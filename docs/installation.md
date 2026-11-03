# Installation

## Agent Skill

```sh
npx skills add tangsangsimida/embedded-debug-skill --skill embedded-debug -a codex -g -y
npx skills add tangsangsimida/embedded-debug-skill --skill embedded-debug -a claude-code -g -y
```

The Skills CLI installs Skill files to the selected agent. Updating and removing can be done with `npx skills update embedded-debug -g -y` and `npx skills remove embedded-debug -g -y` (select the agent when needed). CI exercises Codex and Claude Code installs on Windows, Linux, and macOS, then invokes the installed launcher and packaged CLI. These checks prove file placement and command startup, not natural-language activation by a running model or USB hardware access.

## Python CLI

The CLI is a separate Python distribution. After user authorization, the bundled version-checking launcher can install from the immutable GitHub commit pinned in the Skill using `uvx` or Python pip. Its isolated cache uses the platform's user cache directory and the correct virtual-environment `bin` or `Scripts` layout. Windows/Linux/macOS package and mock-session support is verified by CI; physical USB drivers and MCU connections are not. The package does not install GDB, OpenOCD, USB drivers, or alter PATH. For system tools, consult the [Arm GNU Toolchain project](https://gitlab.arm.com/tooling/gnu-devtools-for-arm) and [OpenOCD installation guide](https://openocd.org/pages/getting-openocd.html); choose versions suitable for the current project and configure PATH yourself. See `edbg doctor --json` for discovery.

Per-user session data uses the platform state directory. Use `edbg --session bench-a connect ...` and repeat `--session bench-a` for subsequent commands to keep independent targets isolated. `edbg session list` reports active and stale named session records. A GDB endpoint already claimed by another named session is rejected.
