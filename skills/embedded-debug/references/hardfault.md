# HardFault and Cortex-M exception diagnosis

## Collection and decoding contract

Cortex-M fault analysis is architecture-specific and must start with confirmed
GDB architecture/capability evidence. A complete offline snapshot contains:

- SCB `CFSR` and `HFSR` values, with names and raw values retained;
- exception LR/`EXC_RETURN`, validated against supported encodings;
- the selected MSP/PSP and a complete basic stacked frame (`r0-r3`, `r12`,
  `lr`, `pc`, `xPSR`); and
- ELF/Git/tool identities needed to map the stacked PC to source.

Do not treat live LR or live PC as the stacked fault PC. Do not infer an invalid
stack pointer merely because a backtrace is short. Separate observed values,
fault classification, hypotheses, and follow-up checks.

`edbg fault-analyze --snapshot <saved-edbg-snapshot.json>` is deterministic and
offline. It decodes only supplied CFSR/HFSR values, EXC_RETURN, and a complete
stacked frame; it never reads target memory. Its result distinguishes
`snapshot_validity` from `fault_classification`; `root_cause` remains unknown
until source-level evidence or reproducible validation supports it. Missing or
unsupported architecture/frame data must remain incomplete/unknown with
limitations. When top-level GDB architecture is `auto`, a matching `arch` field
in a GDB backtrace frame is acceptable evidence; probe identity is never used.

## Diagnostic sequence

1. Confirm the target stopped because of a fault and record the stop event,
   target state, architecture, and session identity.
2. Capture SCB registers and exception LR without modifying the target. If the
   selected stack is unreadable or the frame is incomplete, report that fact
   instead of manufacturing a frame.
3. Decode the stacked PC and xPSR, then map the PC through the exact ELF. Keep
   raw register output and source lookup as separate evidence records.
4. Classify status bits (usage, bus, memory-management, or forced fault).
   Classification is not a root-cause claim; list competing explanations such
   as corrupted return state, invalid stack bounds, or a bad memory access.
5. Validate one hypothesis by a source check, controlled reproduction, or
   regression test. Only then raise root-cause confidence to `confirmed`.

Common rejected shortcuts are using the live PC as the fault PC, calling a
valid status register proof of the offending source line, and treating a mock
snapshot or offline decoder as physical HIL evidence. Retain exact repository
commit, ELF SHA-256, GDB/OpenOCD versions, authorization source, and cleanup
result for any hardware diagnosis.
