# Setup

Run `edbg discover --json` in the project first. It checks VS Code `launch.json`/`tasks.json`, Make/CMake/PlatformIO files, ELF files, and common active local GDB Server ports. Then run `edbg doctor --json` to inspect PATH and versions. Neither command modifies the project or connects to hardware. A project TOML file is optional; CLI overrides it, and project/IDE discovery is used when it is absent. A missing optional GDB/OpenOCD tool is a diagnostic result, not a crash.

Example:

```sh
edbg connect --interface interface/cmsis-dap.cfg --target target/your-target.cfg --elf build/firmware.elf
```

Alternatively pass `--openocd-config debug/openocd.cfg`. When running the Skill from an installed agent location, resolve `scripts/run_edbg.py` relative to the loaded Skill directory, not the user's project working directory. OpenOCD binds to loopback by default. Reuse a running service explicitly with `--reuse-server`. A configured GDB executable must match the target architecture. Avoid simultaneous probe owners.

If the CLI is missing, the bundled `scripts/run_edbg.py` checks the pinned version. With explicit user authorization, invoke it with `--install-python-cli`; it prefers `uvx` and otherwise installs from the matching immutable GitHub tag into the user Python environment. This tag becomes available only after the stable Release gate passes. System GDB/OpenOCD are independently managed by the user.
