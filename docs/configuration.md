# Configuration

No project config file is required. `edbg discover --json` reads the active project's IDE launch/task files, build configuration, ELF outputs, and common running OpenOCD ports. Existing config is the default source; `edbg.toml` is an optional strict override. Unknown TOML keys are rejected. Tool path precedence is CLI override, project TOML, then PATH. OpenOCD host must be loopback. Complete configs can be passed with `--openocd-config`; otherwise use existing interface/target/board settings. Additional OpenOCD commands can be repeated CLI arguments or TOML strings.

Build commands are arrays such as `command = ["cmake", "--build", "build"]` to avoid implicit shell execution. GDB can differ per target with `--gdb` or `[tools].gdb`.
