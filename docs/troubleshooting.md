# Troubleshooting

- `arm-none-eabi-gdb not found`: install a GDB matching the ELF architecture or configure `[tools].gdb` / `--gdb`; rerun `edbg doctor`. The project does not install it.
- `OpenOCD executable not found`: install/select the user's OpenOCD build and rerun doctor.
- OpenOCD exits early: inspect `.edbg/openocd.log`, verify config paths exist in that exact OpenOCD installation, and check probe ownership/permissions.
- GDB cannot connect: verify configured GDB port, loopback service, and target initialization; keep target-specific diagnosis in OpenOCD logs.
- Unknown capability: this means no safe/authoritative probe confirmed it. Do not assume the operation is supported.
- MI timeout/disconnect: target response may be ambiguous; do not blindly replay flash, reset, or write commands. Inspect target state first.
