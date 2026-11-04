# Memory and corruption diagnosis

Start with symbols, linker/map data, stack bounds, and the failing address. Use
memory reads only when the target is confirmed halted and the access has no
known read side effect. Correlate invalid pointers, stack growth, guard values,
heap failures, and DMA ownership with source and build artifacts.

Do not assume a register or memory map from an MCU family name. Treat alignment,
access permissions, cache/MPU configuration, and stale DMA buffers as separate
hypotheses. Memory writes, watchpoints, and cache-affecting operations require
explicit authorization and must be called out in the report.
