# Repository instructions

- Keep target support OpenOCD-driven; do not add vendor/model allowlists or hardcoded chip maps.
- Preserve unknown capability states unless actual backend responses prove support.
- Do not modify or install host system toolchains as part of project setup.
- Hardware access is read-only by default; destructive tests are explicit opt-in.
- Formal releases are blocked until `ci.yml`, `skill-validation.yml`, and `skill-install-test.yml` pass on the exact release candidate.
- Discover and reuse existing project build/flash commands, IDE launch settings, OpenOCD invocations, ELF files, and reachable local GDB servers before requesting configuration; `.edbg.toml` and standalone target scripts are optional.
- Keep the Python core portable across Windows, Linux, and macOS; place filesystem, locking, process identity, IPC, and interrupt differences behind `src/embedded_debug/platforms/`.
- Use `platformdirs` for per-user state/cache paths, `portalocker` for interprocess locks, and `psutil` for cross-platform process identity/reaping. Do not add direct `/proc`, `fcntl`, or POSIX-only cleanup to core services.
- Use authenticated loopback IPC only. The session token is private state; never treat an open local TCP port as authorization.
- Isolate simultaneous target sessions with `edbg --session <name>` and refuse to attach a second manager to an endpoint already owned by another named session.
- Cross-platform claims require Windows, Linux, and macOS CI. CI/mock success is not physical USB adapter or MCU validation.
