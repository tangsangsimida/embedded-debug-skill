# Physical Hardware Test Platform

This repository has a recorded read-only probe check for a physical STM32H743 development board through a WCH-Link. The observations below were verified on 2026-11-03; probe presence and mode must be re-detected before each session. This fixture does not replace portable unit tests or CI.

## Observed Platform

- MCU: STM32H743, as identified by the user. The board model/revision and exact H743 part suffix are not recorded.
- Probe: WCH-Link in ARM/DAP-WINUSB mode; observed USB ID `1a86:8012`, CMSIS-DAP v2 firmware `2.0.0`.
- Link: SWD at 100 kHz was sufficient for the recorded probe. The SWD DPIDR read as `0x6BA02477`.
- Core: OpenOCD examined a Cortex-M7 r1p1. CPUID read as `0x411FC271`.
- MCU identity: DBGMCU_IDCODE at `0xE00E1000` read `0x20036450` (`DEV_ID=0x450`, `REV_ID=0x2003`). ST documents `0x450` for STM32H742/H743/753/H750 and `0x2003` as revision V in [RM0433](https://www.st.com/resource/en/reference_manual/dm00314099-stm32h742-stm32h743753-and-stm32h750-value-line-advanced-armbased-32bit-mcus-stmicroelectronics.pdf). This ID is shared across devices; the exact H743 SKU is user-reported, not uniquely proven by this register.
- Host tools observed during this check: the interactive user's PATH resolves `/home/dennis/.local/bin/openocd` to OpenOCD `0.12.0+dev-02707-g387e40db1-dirty (2026-09-22-09:26)`; `/usr/bin/openocd` reports OpenOCD `0.12.0`. Privileged commands must name `/usr/bin/openocd` explicitly because `sudo` may use a different secure PATH than the invoking shell. `gdb-multiarch` 17.1 (MI2 works) and ARM GCC were also present. These are recorded observations, not current PATH guarantees. USB device permissions required elevated OpenOCD access on the observed host.

## Verified Read-Only Probe

The following command successfully enumerated the adapter, examined the target/core, and read the identity and CPUID registers. It does not reset or flash the board. The empty `examine-end` handler is intentional: the stock STM32H7 target script's handler writes debug-control registers, so keep this override for this read-only check.

```sh
sudo /usr/bin/openocd \
  -f interface/cmsis-dap.cfg \
  -c 'cmsis_dap_vid_pid 0x1a86 0x8012' \
  -c 'cmsis_dap_backend usb_bulk' \
  -c 'transport select swd' \
  -f target/stm32h7x.cfg \
  -c 'stm32h7x.cpu0 configure -event examine-end {}' \
  -c 'adapter speed 100' \
  -c 'init' \
  -c 'targets' \
  -c 'stm32h7x.ap2 mdw 0xE00E1000 1' \
  -c 'stm32h7x.cpu0 mdw 0xE000ED00 1' \
  -c 'shutdown'
```

Do not add `cmsis-dap info`: it is not a valid command in the observed OpenOCD build (it failed with `invalid command name "cmsis_dap"` before the register reads ran). Adapter details were obtained from OpenOCD startup output and USB enumeration. `cmsis_dap_vid_pid` is accepted by the observed build but marked deprecated; verify syntax before adapting this command to another OpenOCD version. Recheck `lsusb` before each run because the WCH-Link mode changes its USB identity. The adapter was previously observed as `1a86:8010` in a mode that this CMSIS-DAP driver could not use.

## Scope and Safety

Confirmed: USB probe enumeration, CMSIS-DAP adapter information, SWD DPIDR, Cortex-M7 examination, and the two read-only register values above. The hardware run below additionally confirms an ELF was programmed and verified, reset into execution, and advanced its application loop counter. The later recorded CLI/GDB-MI test covers source-level inspection, breakpoint hit, stepping and the generic HIL runner; the physical LED observation was reported by the user. The supplied schematic was checked for the HSE crystal and PC13 LED paths only; other circuits and actual board wiring have not been independently validated.

## Authorized Firmware Run (2026-11-05)

The user-authorized, locally ignored fixture at `.local-hardware-tests/stm32h743-debug-sample/cubemx-generated/EdbgH743CubeMX` was used for a programming and run check.

- Re-detected WCH-Link as `1a86:8012`, CMSIS-DAP v2, serial `453A9AEAF5E3`; OpenOCD examined Cortex-M7 and reported 2 MiB flash.
- ARM GCC build succeeded. OpenOCD's `program ... verify reset exit` reported `Verified OK`, then reset the target into run mode.
- During a subsequent run check, `edbg_sample_ticks` advanced from `0x83` to `0x86` over 1.2 seconds of execution. The target was resumed after sampling and left running. This confirms the firmware passed clock initialization and reached its main loop; the 480 MHz rate was configured in source but not independently measured.
- Read-only checks while halted found `SystemCoreClock=480000000`, RCC SWS selecting PLL1 with PLL1 ready, PC13 configured as output (`GPIOC_MODER` mode 01) and push-pull (`GPIOC_OTYPER` bit 13 clear). The GPIO output latch changed `0x0000 -> 0x2000 -> 0x2000 -> 0x0000` across 200 ms samples, consistent with the firmware's 500 ms toggle interval.
- The user-provided schematic identifies the 25 MHz crystal at PH0/PH1 and the PC13 user LED connected to 3.3 V through R9 (1 kOhm), making it active-low. The user then confirmed observing the LED blink on the physical hardware; this is user-observed board behavior. The GPIO latch transitions provide independent software-side evidence consistent with that observation.
- This check used OpenOCD directly, not the EmbeddedDebug GDB/MI session or `edbg` CLI. No breakpoints or source-level variable inspection were performed.

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

Run the read-only stages with `env EDBG_HARDWARE_TESTS=1 EDBG_HARDWARE_CONFIG=/path/to/hil.json pytest -m hardware`. The runner expects the target already halted before stop-mode reads. For an explicitly authorized run-control check, set `authorize_run_control` to `true` in the reviewed config and separately set `EDBG_HARDWARE_RUN_CONTROL=1`; the test will halt, single-step, continue, pause again, and stop its session. The CLI additionally requires `--authorize-run` for each halt/step/continue. Breakpoint insertion/removal likewise requires `authorize_breakpoints: true` in the HIL file and `--authorize` on each CLI command. Do not enable either option with firmware that may control actuators or safety-relevant outputs. The recorded run below exercised this source-level HIL flow on one candidate; it does not establish support for other probes, targets or software versions.


## Authorized `edbg` GDB/MI validation (2026-11-05)

- Tested exact merged candidate `09db2cf54b21ac2c8dfe83a5520d302d934196a3` on Linux, with WCH-Link `1a86:8012`, OpenOCD 0.12.0 and GDB 17.1. The existing CubeMX sample ELF SHA-256 was `0f32ad9bf5191723655d8a48f0ed896c4e0601507d36855279be63c464e216fb`. The MI trace used a stdio logging proxy around `/usr/bin/gdb-multiarch`.
- `edbg` connected with the existing CMSIS-DAP and STM32H7 OpenOCD scripts and the `examine-end` override shown above. It read registers, backtrace and `edbg_sample_ticks`; inserted and actually hit `HAL_GetTick`, removed the breakpoint, stepped, continued, halted, resumed and stopped the session. The target was resumed after the separate HIL-runner test.
- The opt-in `tests/hardware/test_hardware_opt_in.py` run passed (1 test) against the exact merged candidate with explicitly authorized run control and breakpoint operations. The current raw MI trace is retained as `.local-hardware-tests/review-hil/master-09db2cf-mi.log`; command/JSON output, OpenOCD manager logs, ELF hash and tool versions are retained in the local ignored `.local-hardware-tests/review-hil/` directory. These files are local evidence, not packaged release artifacts.
- This verifies only the named candidate, fixture and commands. A later source change or release candidate needs its own checks; CI and the test above do not prove other OS/adapter/target combinations.
