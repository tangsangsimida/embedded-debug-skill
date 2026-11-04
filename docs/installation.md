# Installation

## Agent Skill

```sh
npx skills add tangsangsimida/embedded-debug-skill --skill embedded-debug -a codex -g -y
npx skills add tangsangsimida/embedded-debug-skill --skill embedded-debug -a claude-code -g -y
```

The Skills CLI installs Skill files to the selected agent. Updating and removing can be done with `npx skills update embedded-debug -g -y` and `npx skills remove embedded-debug -g -y` (select the agent when needed). CI exercises Codex and Claude Code installs on Windows, Linux, and macOS, then invokes the installed launcher and packaged CLI. These checks prove file placement and command startup, not natural-language activation by a running model or USB hardware access.

## Python CLI

The CLI is a separate Python distribution. After user authorization, the bundled version-checking launcher can install from the immutable GitHub commit pinned in the Skill using `uvx` or Python pip. Its isolated cache uses the platform's user cache directory and the correct virtual-environment `bin` or `Scripts` layout. Windows/Linux/macOS package and mock-session support is verified by CI; physical USB drivers and MCU connections are not. The package does not install GDB, OpenOCD, USB drivers, or change system-wide PATH. For system tools, consult the [Arm GNU Toolchain project](https://gitlab.arm.com/tooling/gnu-devtools-for-arm) and [OpenOCD installation guide](https://openocd.org/pages/getting-openocd.html); choose versions suitable for the current project. If you explicitly authorize a persistent PATH change, `edbg environment path-add --directory <existing-bin-dir> --shell <bash|zsh|fish> --authorize` adds a reversible managed block to that user's shell file. Remove it with `edbg environment path-remove --shell <bash|zsh|fish> --authorize`. See `edbg doctor --json` for discovery.

Per-user session data uses the platform state directory. Use `edbg --session bench-a connect ...` and repeat `--session bench-a` for subsequent commands to keep independent targets isolated. `edbg session list` reports active and stale named session records. A GDB endpoint already claimed by another named session is rejected.

For a tool archive independently reviewed and approved by the user, `edbg environment install-tool-archive --kind gdb --source <local-path-or-https-url> --sha256 <reviewed-sha256> --executable <path-inside-archive> --authorize` can extract and verify it in a per-user isolated cache. Use `--kind openocd` for OpenOCD. The digest must come from a trusted, independently verified source: a matching checksum alone does not establish provenance. ZIP and tar files containing only regular files and directories are supported; archives containing links or special files are rejected. The command checks `--version` and, for GDB, MI2; it does not prove target compatibility or install drivers. Pass the returned absolute path with `--gdb` or `--openocd` for a session; no global PATH is changed. `edbg environment remove-tool-archive --sha256 <digest> --authorize` removes the managed cache after checking recorded debug sessions. Close other applications using the tool before removal.
