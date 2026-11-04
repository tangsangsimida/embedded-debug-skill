# Communication diagnosis

Separate software framing, queue/buffer ownership, interrupt/DMA delivery, and
physical-layer configuration into independent hypotheses. Use source, logs,
symbol values, and backend-proven peripheral state together. Record baud/clock,
timeouts, error flags, and the exact transaction boundary when known.

Never claim a bus or electrical fault from firmware observations alone. Request
human measurements when logic-analyzer, scope, termination, wiring, or signal
integrity evidence is required.
