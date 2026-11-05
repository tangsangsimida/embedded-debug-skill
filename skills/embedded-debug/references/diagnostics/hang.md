# Hang, dead loop, or blocked execution

## Evidence-first playbook

1. Record the project/ELF identity and prove whether the target is connected,
   running, halted, or unknown. A stale `halted` state is not evidence of a
   current stop.
2. If the target is running, obtain explicit run-control authorization before
   halting it. Capture the first PC, thread selection (if reported), and a
   backtrace. Keep the raw CLI/MI output and timestamp.
3. Resume only with explicit authorization and a reviewed safe-state plan. Take
   a second sample after a stated interval. Record both target states and the
   exact interval; two samples at the same PC are useful only if the target was
   actually allowed to run between them.
4. Compare the samples against symbols and source. Classify the observation as
   a loop, lock/semaphore wait, interrupt-dependent wait, watchdog/reset path,
   or an unknown stop.
5. For an RTOS, use only thread data exposed by GDB/OpenOCD. A generic GDB
   thread group does not prove scheduler state, lock ownership, or priority
   inversion. Report those facts as unknown until a compatible backend provides
   them.

## Minimum evidence and conclusions

```text
sample_a: state, timestamp, pc, thread, backtrace
sample_b: state, timestamp, pc, thread, backtrace
interval: seconds and whether run control was authorized
comparison: unchanged frame / changed frame / unavailable
root_cause: observed|confirmed|inferred|unknown
```

“Same PC” alone is not a confirmed deadlock: it can be a normal polling wait,
a breakpoint stop, or a fault handler. Do not step a safety-relevant or
actuator-controlling target without explicit authorization and a reviewed
safe-state plan. If the state becomes `unknown` or `disconnected`, stop using
old samples and recover the session before collecting more evidence.
