# RTOS diagnosis

Treat RTOS awareness as backend capability data, not as a claim inferred from
the presence of a scheduler symbol. First record architecture, RTOS name/version
when discoverable, and the exact GDB/OpenOCD response. Then collect only the
threads, current task, states, priorities, stacks, and synchronization data the
backend exposes.

Do not decode private control blocks without matching version/layout evidence.
If thread enumeration is generic or incomplete, report that limitation and use
source, stack samples, and synchronization call paths instead. Distinguish a
task blocked by design from a scheduler failure with timing and state evidence.
