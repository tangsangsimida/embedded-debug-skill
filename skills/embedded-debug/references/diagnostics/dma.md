# DMA diagnosis

Correlate the descriptor/buffer addresses, transfer direction, count, completion
flags, error flags, cache policy, and ownership hand-off with source and linker
placement. Verify the target is halted before reads and identify whether a
status read has side effects.

Do not infer a DMA register layout from a different MCU or vendor. Prefer a
non-destructive repeated observation; resets, flag clears, buffer writes, and
restarts require explicit authorization.
