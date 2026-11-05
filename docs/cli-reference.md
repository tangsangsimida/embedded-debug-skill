# CLI reference

`edbg --help`, `edbg --version`, `edbg doctor --json`, `edbg discover --json`, `edbg config validate --json`, `edbg probe list --json`, and `edbg connect` are available. Connect discovers IDE OpenOCD arguments and existing ELF files when unambiguous. It automatically reuses an open local GDB Server, then asks GDB to validate the target connection. A standalone `target.cfg` and `.edbg.toml` are optional; an existing board/full config, IDE launch entry, or OpenOCD command is sufficient. Explicit CLI settings override the optional project TOML. Global tool overrides are `--gdb` and `--openocd`; `--config` selects optional TOML; `--session <name>` selects an isolated persistent target context. Connect supports complete configs, interface/target/board, extra OpenOCD commands, loopback host, GDB port, adapter speed, and ELF.

`--json` emits `success`, `command`, `timestamp`, `target`, `target_state`, `data`, `error`, and `diagnostics`. Exit code 0 means command/diagnostic completed; 2 indicates rejected or failed operation; 1 indicates a build failure. A successful `doctor` command may report missing optional tools in its data.

Set `EDBG_EVIDENCE_FILE=/path/to/trace.jsonl` to opt into an append-only,
machine-readable evidence record for every emitted CLI envelope. Records use the
Skill evidence fields and preserve unknown authorization/state instead of
guessing. The file is protected by an interprocess lock; a recording failure is
reported on stderr but does not change the debug operation result. This captures
CLI evidence, not raw GDB/OpenOCD logs; retain those artifacts separately when a
diagnosis needs them. Session-backed records also include a `provenance` object
with session identity, executable paths, ELF path and SHA-256 when available;
tool version fields remain `null` unless the backend reports them. Set
`EDBG_EVIDENCE_ARTIFACT_DIR` as well to persist each raw CLI envelope as an
individual JSON artifact; `raw_artifact` then contains the artifact filename
relative to that directory. Artifact-writing failures are reported without
changing the debug operation result.

`edbg fault-analyze --snapshot <file>` performs offline Cortex-M fault-frame
analysis from a saved snapshot envelope. It does not connect to hardware; an
unsupported architecture or incomplete frame is returned with a non-valid
`snapshot_validity` and limitations. A valid snapshot reports a
`fault_classification`, but `root_cause` remains unknown until additional
source-level or reproduction evidence is supplied; snapshot decoding alone is
not a root-cause proof.

`edbg environment inspect --json` independently discovers the Python runtime, ARM GCC/GDB, OpenOCD, objcopy, and size utilities. CLI overrides, project tool paths, then PATH take precedence. `edbg environment plan --json` returns advisory install and PATH guidance only. `edbg environment path-add --directory <existing-bin-dir> --shell <bash|zsh|fish> --authorize` adds an idempotent marked PATH block to the selected current-user shell file; `environment path-remove --shell <bash|zsh|fish> --authorize` removes only that block. Both reject missing authorization and audit actual changes. `edbg environment install-cli --source <pip-source> --authorize` installs the explicitly selected Python CLI into a versioned user cache and verifies its version and source digest against the running Skill; it never installs embedded system tools. A failed install removes only its incomplete versioned environment; previous CLI versions remain available for rollback.

`environment install-tool-archive` accepts a reviewed local or HTTPS ZIP/tar source, independently obtained SHA-256, archive-relative executable and `gdb` or `openocd` kind; `--authorize` is required. It extracts into a private user cache, rejects links and special files, checks the executable, and returns its session-specific path. `environment remove-tool-archive --sha256 <digest> --authorize` rolls back a managed cache entry. See [installation](installation.md) for the trust and archive-format limits.

Build discovery supports existing VS Code build tasks, CMake, Make, and PlatformIO. Build and flash commands are executed as argument arrays only after explicit workspace-command authorization; flashing also requires operation authorization. Register/variable/stack/breakpoint/memory commands use one persistent GDB/MI session across CLI processes. `edbg variable read` accepts simple identifier/member paths only; arbitrary GDB expressions may execute code or mutate target state and require `--authorize-expression`. `edbg session list` lists named target sessions; use `edbg --session bench-a connect ...` and pass the same session selector on later commands. A GDB host/port cannot be claimed by more than one active session. `edbg --session bench-a session cancel --authorize-run` interrupts an in-flight GDB/MI operation from another CLI process; this can halt target execution. Halt, continue, step/next, reset, memory writes, flash, and breakpoint add/remove require their corresponding explicit flags (`--authorize-run` for run control, `--authorize` for target modifications). The daemon repeats the same checks so direct session requests cannot bypass the CLI guard. Offline Cortex-M HardFault frame decoding is available from saved snapshots; live fault collection, SVD, RTOS-specific thread decoding, and telemetry remain incomplete.

The runtime uses platform-specific state/cache directories and process controls behind one adapter layer. Session IPC binds to local loopback and authenticates each request with a random token stored in per-user private state. CI tests Windows, Linux, and macOS software/mock behavior; it does not validate USB access or physical targets.
