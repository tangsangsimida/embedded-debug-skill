# Hang, dead loop, or blocked execution

1. Prove whether the target is connected, running, halted, or unknown.
2. With explicit run-control authorization, take at least two separated samples
   of PC and stack/call stack; one sample cannot distinguish a loop, lock,
   normal wait, and fault stop.
3. Compare the samples with source and symbols. Look for unchanged loop guards,
   lock/semaphore waits, interrupt-dependent progress, and watchdog paths.
4. For an RTOS, use only thread data actually exposed by GDB/OpenOCD; generic
   GDB thread groups do not prove scheduler or synchronization state.

Record the sampling interval, target state transitions, and rejected causes.
Do not step a safety-relevant or actuator-controlling target without explicit
authorization and a reviewed safe-state plan.
