# CLI reference

`edbg --help`, `edbg --version`, `edbg doctor --json`, `edbg discover --json`, `edbg config validate --json`, `edbg probe list --json`, and `edbg connect --elf ...` are available. Connect reuses a detected local GDB server and reads IDE OpenOCD arguments when unambiguous. Explicit CLI settings override the optional project TOML. Global tool overrides are `--gdb` and `--openocd`; `--config` selects TOML. Connect supports complete configs, interface/target/board, extra OpenOCD commands, loopback host, GDB port, adapter speed, server reuse, and ELF.

`--json` emits `success`, `command`, `timestamp`, `target`, `target_state`, `data`, `error`, and `diagnostics`. Exit code 0 means command/diagnostic completed; 2 indicates rejected or failed operation; 1 indicates a build failure. A successful `doctor` command may report missing optional tools in its data.

Build discovery supports existing VS Code build tasks, CMake, Make, and PlatformIO. Flash discovery can reuse an existing VS Code task and requires `--authorize`; otherwise GDB's `load` is available when explicitly authorized. Register/variable/stack/breakpoint/memory commands connect through GDB/MI. Memory writes, flash, reset, and running/stepping require explicit flags. Persistent cross-process session management, SVD, validated HardFault decoding, RTOS-specific thread decoding, and telemetry remain incomplete.
