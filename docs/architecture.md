# Architecture

EmbeddedDebug-Skill treats OpenOCD configuration and GDB target description as the target adapter. Python code does not classify MCU vendor/model strings or carry device memory maps. The core layers are CLI/envelopes, validated TOML, OpenOCD process lifecycle, GDB/MI transport/parser, controller operations, capability reporting, and optional architecture/RTOS/telemetry extensions.

The MI transport owns one subprocess, monotonically increments command tokens, serializes writes, routes asynchronous records without mistaking them for command results, applies command timeouts, tracks run state, and supports deterministic process cleanup. A timed-out command does not retry automatically because its target-side effect may be ambiguous.

Operating-system behavior is isolated in `platforms/`. `platformdirs` selects per-user state and cache roots; `portalocker` supplies cross-platform startup/manager locks; `psutil` verifies process creation time, executable, user, and command line before cleanup. The GDB and OpenOCD backends keep a shared implementation, with process creation and GDB interrupt behavior delegated to the selected adapter.

Each named debug session owns a separate state directory, manager lock, GDB process, and IPC token. `edbg --session <name>` selects a target context; the default name preserves the original single-session CLI behavior. Session startup is serialized through a short-lived global registry lock and rejects a GDB host/port already managed by another session. Session IPC uses a random token over TCP bound only to `127.0.0.1`; the endpoint alone grants no authority. Existing schema-2 Unix-socket sessions remain readable on POSIX during upgrade.

Windows uses a new subprocess process group and `CTRL_BREAK_EVENT` for an active GDB interrupt. Linux/macOS use their native subprocess/signaling behavior through the common adapter. Owned-process cleanup verifies identity before terminating only the owned process tree. User-provided services are never terminated.

Capability probes are conservative. Register names and target architecture come from GDB responses. Unsafe address-zero reads are not used to probe memory. RTOS thread groups alone do not prove RTOS awareness. Flash driver support requires target-specific evidence and remains unknown until queried. A future plugin registry will attach architecture-specific diagnostics after architecture discovery.

OpenOCD is loopback-bound by default and an already open GDB port is not claimed as an owned child process. User-provided full config, interface, target, board, and command arguments are passed to OpenOCD. Competing debug-server instances are avoided by port checking, though multi-process probe ownership cannot be guaranteed across other tools.
