# Physical Hardware Test Platform

This repository has a recorded read-only probe check for a physical STM32H743 development board through a WCH-Link. The observations below were verified on 2026-11-03; probe presence and mode must be re-detected before each session. This fixture does not replace portable unit tests or CI.

## Observed Platform

- MCU: STM32H743, as identified by the user. The board model/revision and exact H743 part suffix are not recorded.
- Probe: WCH-Link in ARM/DAP-WINUSB mode; observed USB ID `1a86:8012`, CMSIS-DAP v2 firmware `2.0.0`.
- Link: SWD at 100 kHz was sufficient for the recorded probe. The SWD DPIDR read as `0x6BA02477`.
- Core: OpenOCD examined a Cortex-M7 r1p1. CPUID read as `0x411FC271`.
- MCU identity: DBGMCU_IDCODE at `0xE00E1000` read `0x20036450` (`DEV_ID=0x450`, `REV_ID=0x2003`). ST documents `0x450` for STM32H742/H743/753/H750 and `0x2003` as revision V in [RM0433](https://www.st.com/resource/en/reference_manual/dm00314099-stm32h742-stm32h743753-and-stm32h750-value-line-advanced-armbased-32bit-mcus-stmicroelectronics.pdf). This ID is shared across devices; the exact H743 SKU is user-reported, not uniquely proven by this register.
- Host tools observed during this check: OpenOCD 0.12.0 development build, `gdb-multiarch` 17.1 (MI2 works), and ARM GCC. These are recorded versions, not current PATH or install-path guarantees. USB device permissions required elevated OpenOCD access on the observed host.

## Verified Read-Only Probe

The following command successfully enumerated the adapter, examined the target/core, and read the identity and CPUID registers. It does not reset or flash the board. The empty `examine-end` handler is intentional: the stock STM32H7 target script's handler writes debug-control registers, so keep this override for this read-only check.

```sh
sudo openocd \
  -f interface/cmsis-dap.cfg \
  -c 'cmsis_dap_vid_pid 0x1a86 0x8012' \
  -c 'cmsis_dap_backend usb_bulk' \
  -c 'transport select swd' \
  -f target/stm32h7x.cfg \
  -c 'stm32h7x.cpu0 configure -event examine-end {}' \
  -c 'adapter speed 100' \
  -c 'init' \
  -c 'cmsis-dap info' \
  -c 'targets' \
  -c 'stm32h7x.ap2 mdw 0xE00E1000 1' \
  -c 'stm32h7x.cpu0 mdw 0xE000ED00 1' \
  -c 'shutdown'
```

`cmsis_dap_vid_pid` is accepted by the observed OpenOCD build but marked deprecated there; verify syntax before adapting this command to another OpenOCD version. Recheck `lsusb` before each run because the WCH-Link mode changes its USB identity. The adapter was previously observed as `1a86:8010` in a mode that this CMSIS-DAP driver could not use.

## Scope and Safety

Confirmed: USB probe enumeration, CMSIS-DAP adapter information, SWD DPIDR, Cortex-M7 examination, and the two read-only register values above. No source-level GDB session, ELF loading, breakpoints, stepping, application behavior, peripherals, or test harness has been validated. No board-specific wiring, reset line, external sensors, actuators, or power arrangement is documented.

Re-detect the probe and target on every session. Keep ordinary hardware tests read-only. Obtain explicit authorization before reset, halt/continue, flash/erase, memory or register writes, or running firmware that can actuate hardware. Never infer pin wiring or board capabilities from the MCU family. Use a user-provided firmware project/ELF and existing board settings for source-level debugging; do not invent them.

## Opt-in HIL workflow

The generic HIL runner is `tests/hardware/hil_runner.py`; the pytest entry point is skipped unless both opt-in variables are set. Supply the project's existing connection parameters and a matching ELF if checking expressions:

```json
{
  "cli": "edbg",
  "session": "hardware-validation",
  "global_args": ["--gdb", "gdb-multiarch"],
  "connect_args": ["--reuse-server", "--elf", "build/firmware.elf"],
  "expressions": ["main"],
  "breakpoints": [],
  "run_control": false
}
```

Run the read-only stages with `env EDBG_HARDWARE_TESTS=1 EDBG_HARDWARE_CONFIG=/path/to/hil.json pytest -m hardware`. The runner expects the target already halted before stop-mode reads. For an explicitly authorized run-control check, set `authorize_run_control` to `true` in the reviewed config and separately set `EDBG_HARDWARE_RUN_CONTROL=1`; the test will halt, single-step, continue, pause again, and stop its session. The CLI additionally requires `--authorize-run` for each halt/step/continue. Breakpoint insertion/removal likewise requires `authorize_breakpoints: true` in the HIL file and `--authorize` on each CLI command. Do not enable either option with firmware that may control actuators or safety-relevant outputs. This fixture has not had the source-level HIL flow run; only the previously documented adapter/core/register probe observations are confirmed.
