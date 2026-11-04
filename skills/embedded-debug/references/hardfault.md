# Fault diagnosis

Cortex-M fault analysis is architecture-specific and must only run after GDB architecture/capability detection. A valid analysis needs stacked exception context, EXC_RETURN interpretation, selected MSP/PSP, xPSR, and SCB fault registers when the target exposes them. Do not treat live LR as the fault PC. Separate observed values, deductions, and follow-up checks.

`edbg fault-analyze --snapshot <saved-edbg-snapshot.json>` provides a
deterministic offline decoder for confirmed Cortex-M architecture responses. It
decodes only supplied CFSR/HFSR values, EXC_RETURN, and a complete stacked frame;
it never reads target memory or treats live LR/PC as the stacked fault PC. Missing
or unsupported architecture/frame data returns `unknown` with limitations. When
the top-level GDB architecture is `auto`, a matching `arch` field in a GDB
backtrace frame is acceptable evidence; probe identity is never used. Use
the normal GDB/manual evidence path to collect a snapshot and retain its exact
commit and tool identity.
