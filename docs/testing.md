# Cross-platform testing

## Shared checks

The CI baseline is Python 3.11. Daily PR/master CI uses one representative runner for each OS family (`ubuntu-24.04`, `windows-2022`, and `macos-15`) and runs Ruff, mypy, the complete pytest suite, wheel/sdist build, `pip check`, and `pip-audit`. The suite exercises Mock GDB/MI, Mock OpenOCD, persistent-session IPC, timeout/recovery behavior, JSON CLI output, and authorization checks.

`skill-install-test.yml` runs a quick Ubuntu installation smoke for Codex and Claude Code on each PR/master push. Its full nine-platform-by-two-agent matrix runs nightly at 02:17 UTC and on `workflow_dispatch`. Both paths verify the installed Skill resources, install the CLI/package, check `--version`, `--help`, and `doctor`, and run the Mock test suite. Formal releases separately run the same full matrix against the exact candidate wheel through `release-gate-reusable.yml`. These checks verify installation and command contracts; they do not verify that a live language model triggers a Skill from natural language.

## Verified runner matrix

Daily CI coverage stays small and spans Linux, Windows, and macOS. The scheduled installation matrix and formal release gate cover:

| Platform family | Nightly and release runners | Python |
| --- | --- | --- |
| Linux x64 | Ubuntu 24.04, Ubuntu 26.04 | 3.11 |
| Linux arm64 | Ubuntu 24.04 arm64 | 3.11 |
| Windows x64 | Windows Server 2022, Windows Server 2025 | 3.11 |
| Windows arm64 | Windows 11 arm64 | 3.11 |
| macOS x64 | macOS 15 Intel | 3.11 |
| macOS arm64 | macOS 15, macOS 26 | 3.11 |

The workflow files are authoritative for the split between daily, nightly, and release checks. Review the exact workflow run for a candidate before claiming it passed; an older green run does not prove a later commit.

## Platform-sensitive coverage

- Process identity uses PID, creation time, executable, and user. Cleanup refuses a mismatched identity and terminates only the verified owned process tree.
- Windows process startup creates a process group; cancellation uses a console control event. Native Windows CI exercises `Scripts` virtual environments, the `edbg.exe` package entry point, Windows paths, and ARM64 process scheduling through the same test suite.
- Linux-specific `/proc` access is limited to migration of old session records. New identities use `psutil` like the other platforms.
- macOS and Linux use the shared POSIX subprocess behavior; macOS CI covers both Intel and Apple Silicon runners.
- IPC binds to IPv4 loopback and each request carries a per-session random token. Tests reject non-loopback endpoints and invalid session credentials.
- File locking and user data directories use `portalocker` and `platformdirs`; tests exercise lock contention and environment overrides.
- Paths are passed as subprocess argument arrays. Tests include Unicode and whitespace in a script path and its arguments.

## Hardware boundary

Hardware integration tests are opt-in with `EDBG_HARDWARE_TESTS=1` and `EDBG_HARDWARE_CONFIG`. Standard CI does not access USB probes or MCUs. Passing mocked tests proves software behavior only; it does not prove that an OpenOCD build, USB driver/permission configuration, adapter firmware, target configuration, or physical board works on a given platform. See [hardware-test-platform.md](hardware-test-platform.md) for any locally observed fixture, and re-detect it before use.

`tests/hardware/hil_runner.py` accepts a user-authored JSON config that points at the project's existing debug setup. It verifies the GDB connection, requires the target to be already halted before register/stack/variable inspection, and cleans up its session and any breakpoints it created. Optional halt/step/continue testing requires both `authorize_run_control: true` in that file and `EDBG_HARDWARE_RUN_CONTROL=1`; the CLI operations additionally require `--authorize-run`. Breakpoint changes require both `authorize_breakpoints: true` and CLI `--authorize`. No reset, flash, or memory/register write is performed by this runner. Variable reads require a matching ELF in `connect_args`. Hardware tests remain unverified until the opt-in test is actually run with a user-provided target/ELF configuration.

## Local commands

```sh
python -m pip install -e '.[dev]'
ruff check .
mypy src/embedded_debug
pytest
python -m build
python -m pip check
python -m pip install --upgrade certifi
pip-audit --skip-editable
```

On Windows PowerShell, invoke the same commands from the repository root; Python entry points installed into a virtual environment are under `Scripts`, and pytest/subprocess tests use argument arrays rather than shell-specific quoting.
