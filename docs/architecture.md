# Architecture

EmbeddedDebug-Skill treats OpenOCD configuration and GDB target description as the target adapter. Python code does not classify MCU vendor/model strings or carry device memory maps. The core layers are CLI/envelopes, validated TOML, OpenOCD process lifecycle, GDB/MI transport/parser, controller operations, capability reporting, and optional architecture/RTOS/telemetry extensions.

The MI transport owns one subprocess, monotonically increments command tokens, serializes writes, routes asynchronous records without mistaking them for command results, applies command timeouts, tracks run state, and supports deterministic process cleanup. A timed-out command does not retry automatically because its target-side effect may be ambiguous.

Capability probes are conservative. Register names and target architecture come from GDB responses. Unsafe address-zero reads are not used to probe memory. RTOS thread groups alone do not prove RTOS awareness. Flash driver support requires target-specific evidence and remains unknown until queried. A future plugin registry will attach architecture-specific diagnostics after architecture discovery.

OpenOCD is loopback-bound by default and an already open GDB port is not claimed as an owned child process. User-provided full config, interface, target, board, and command arguments are passed to OpenOCD. Competing debug-server instances are avoided by port checking, though multi-process probe ownership cannot be guaranteed across other tools.
