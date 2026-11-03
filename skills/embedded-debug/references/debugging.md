# Debugging

Run `edbg --help`, `edbg doctor --json`, and validate the project's TOML before connecting. Read-only commands include register, variable, stack, and safe memory inspection when a live OpenOCD/GDB service is available. Use `edbg connect` with the project's OpenOCD files and ELF. GDB/MI serializes operations; target running state may make ordinary variable reads unavailable. Capabilities not confirmed from responses stay unknown.

Build commands are argument arrays in TOML, never shell strings. Flash and reset require explicit authorization. On disconnect or timeout, report failure and reconnect only when requested/appropriate. For repeatable automation, capture JSON with `--json` and check process exit status.
