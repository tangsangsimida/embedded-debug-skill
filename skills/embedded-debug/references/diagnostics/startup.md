# Startup and post-download failures

Compare the discovered ELF, vector table, reset handler, linker regions,
initialization order, clock setup, and first observable execution point. Verify
the GDB/OpenOCD target connection separately from firmware behavior. Distinguish
an invalid ELF/configuration, a target reset loop, a fault during startup, and a
program that runs but has no visible output.

Do not flash, reset, or run potentially actuator-controlling firmware merely to
probe startup. Those are separate authorized operations, and a failed download
must retain tool output, verification result, and final target state.
