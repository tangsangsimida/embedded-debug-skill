# Fault diagnosis

Cortex-M fault analysis is architecture-specific and must only run after GDB architecture/capability detection. A valid analysis needs stacked exception context, EXC_RETURN interpretation, selected MSP/PSP, xPSR, and SCB fault registers when the target exposes them. Do not treat live LR as the fault PC. Separate observed values, deductions, and follow-up checks. The current release does not implement a validated HardFault decoder; use GDB/manual evidence and report this limitation.
