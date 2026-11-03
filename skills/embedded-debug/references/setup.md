# Setup

Run `edbg discover --json` in the existing firmware project first. It inspects VS Code `launch.json`/`tasks.json`, existing OpenOCD command arguments, Make/CMake/PlatformIO files, ELF files, and common active local GDB Server ports. Then run `edbg doctor --json` to inspect PATH and versions. Neither command modifies the project or connects to hardware. Do not create `.edbg.toml` just to use the Skill: it is an optional override, and project/IDE discovery is used when it is absent. A missing tool is a diagnostic result, not a crash.

Example:

```sh
edbg connect --interface interface/cmsis-dap.cfg --target target/your-target.cfg --elf build/firmware.elf
```

If discovery finds a complete existing IDE or project setup, run `edbg connect` without repeating those settings; an open local GDB Server is reused automatically and GDB validates the connection and target response. Otherwise, pass the project's existing configuration, for example `--openocd-config debug/openocd.cfg` or interface/target/board scripts. A standalone `target.cfg` is not required when an existing board/full config or launch command supplies the target setup. When running the Skill from an installed agent location, resolve `scripts/run_edbg.py` relative to the loaded Skill directory, not the user's project working directory. Newly started OpenOCD binds to loopback by default. A configured GDB executable must match the target architecture. Avoid simultaneous probe owners.

If the CLI is missing, the bundled `scripts/run_edbg.py` checks the pinned version. With explicit user authorization, invoke it with `--install-python-cli`; it prefers `uvx` and otherwise installs from the immutable GitHub commit pinned in the Skill into the user Python environment. The current candidate is not a stable Release. System GDB/OpenOCD are independently managed by the user.
