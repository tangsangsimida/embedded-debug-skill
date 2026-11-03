# Installation

## Agent Skill

```sh
npx skills add tangsangsimida/embedded-debug-skill --skill embedded-debug -a codex -g -y
npx skills add tangsangsimida/embedded-debug-skill --skill embedded-debug -a claude-code -g -y
```

The Skills CLI installs Skill files to the selected agent. Updating and removing can be done with `npx skills update embedded-debug -g -y` and `npx skills remove embedded-debug -g -y` (select the agent when needed). The project's CI runs isolated Codex and Claude Code installer tests. These prove files are placed and resources are complete, not natural-language activation by a running model.

## Python CLI

The CLI is a separate Python distribution. After user authorization, the bundled version-checking launcher can install from the matching immutable GitHub tag using `uvx` or Python pip. A stable `v0.1.0` tag is not available until its release gate is satisfied. The package does not install GDB, OpenOCD, USB drivers, or alter PATH. For system tools, consult the [Arm GNU Toolchain project](https://gitlab.arm.com/tooling/gnu-devtools-for-arm) and [OpenOCD installation guide](https://openocd.org/pages/getting-openocd.html); choose versions suitable for the current project and configure PATH yourself. See `edbg doctor --json` for discovery.
