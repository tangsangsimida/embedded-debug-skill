"""Persistent, serialized GDB/MI subprocess transport."""

from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Sequence
from pathlib import Path

from ..platforms import get_platform
from .mi_parser import MIRecord, parse_record


class GDBError(RuntimeError):
    pass


class GDBTimeout(GDBError):
    pass


class MITransport:
    def __init__(self, executable: str = "arm-none-eabi-gdb", timeout: float = 10.0) -> None:
        self.executable, self.timeout = executable, timeout
        self.process_executable: str | None = None
        self.process: subprocess.Popen[str] | None = None
        self._records: queue.Queue[MIRecord] = queue.Queue()
        self._lock = threading.Lock()
        self._token = 0
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._state_changed = threading.Condition()
        self.state = "disconnected"
        self.async_records: deque[MIRecord] = deque(maxlen=500)
        self.stderr_tail: deque[str] = deque(maxlen=100)

    def start(self, elf: str | Path | None = None, extra_args: Sequence[str] = ()) -> None:
        if self.process and self.process.poll() is None:
            return
        executable = shutil.which(self.executable) or (
            self.executable if Path(self.executable).is_file() else None
        )
        if executable is None:
            raise GDBError(f"GDB executable not found: {self.executable}")
        executable_path = Path(executable)
        python_script = executable_path.suffix.casefold() == ".py"
        process_executable = sys.executable if python_script else executable
        self.process_executable = str(Path(process_executable).resolve())
        args = get_platform().executable_argv(
            executable, "--interpreter=mi2", "--quiet", *extra_args
        )
        if elf:
            args.append(str(elf))
        self.process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            **get_platform().creation_options(),
        )
        identity = get_platform().process_identity(self.process.pid)
        if identity is not None:
            self.process_executable = identity.executable
        self.state = "unknown"
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            try:
                record = parse_record(line)
            except ValueError as exc:
                self._records.put(MIRecord(None, "parse-error", text=str(exc), raw=line))
                continue
            if record:
                self._observe_record(record)
                self._records.put(record)
        with self._state_changed:
            self.state = "disconnected"
            self._state_changed.notify_all()
        self._records.put(MIRecord(None, "eof", text="GDB closed stdout"))

    def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self.stderr_tail.append(line.rstrip("\r\n"))

    def _observe_record(self, record: MIRecord) -> None:
        """Keep run state current even when no command is waiting for MI output."""
        with self._state_changed:
            if record.kind in {"exec", "status", "notify"}:
                self.async_records.append(record)
            if record.kind == "exec" and record.cls == "running":
                self.state = "running"
            elif record.kind == "exec" and record.cls == "stopped":
                self.state = "halted"
            elif record.kind == "notify" and record.cls in {
                "thread-group-exited",
                "thread-group-removed",
            }:
                self.state = "disconnected"
            self._state_changed.notify_all()

    def wait_for_state(self, state: str, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        with self._state_changed:
            while self.state != state:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._state_changed.wait(remaining)
            return True

    def command(self, command: str, timeout: float | None = None) -> MIRecord:
        with self._lock:
            if not self.process or self.process.poll() is not None or not self.process.stdin:
                self.state = "disconnected"
                raise GDBError("GDB session is disconnected")
            self._token += 1
            token = self._token
            self.process.stdin.write(f"{token}{command}\n")
            self.process.stdin.flush()
            deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    with self._state_changed:
                        self.state = "unknown"
                        self._state_changed.notify_all()
                    raise GDBTimeout(f"GDB/MI command timed out: {command}")
                try:
                    record = self._records.get(timeout=remaining)
                except queue.Empty as exc:
                    with self._state_changed:
                        self.state = "unknown"
                        self._state_changed.notify_all()
                    raise GDBTimeout(f"GDB/MI command timed out: {command}") from exc
                if record.kind in {"exec", "status", "notify", "console", "target", "log"}:
                    if record.kind == "exec" and record.cls == "running":
                        self.state = "running"
                    elif record.kind == "exec" and record.cls == "stopped":
                        self.state = "halted"
                    continue
                if record.kind == "eof":
                    self.state = "disconnected"
                    raise GDBError(record.text or "GDB exited")
                if record.kind == "parse-error":
                    raise GDBError(record.text or "Invalid GDB/MI output")
                if record.token == token:
                    if record.cls == "running":
                        self.state = "running"
                    elif record.cls == "done" and command.startswith("-exec-interrupt"):
                        self.state = "halted"
                    if record.cls == "error":
                        message = (record.results or {}).get("msg", "GDB command failed")
                        raise GDBError(str(message))
                    return record
                self.async_records.append(record)

    def cancel(self) -> None:
        """Interrupt a blocked debugger operation; target execution may halt."""
        if not self.process or self.process.poll() is not None:
            raise GDBError("GDB session is disconnected")
        if self._lock.acquire(blocking=False):
            self._lock.release()
            raise GDBError("No in-flight GDB/MI operation is available to cancel")
        try:
            get_platform().interrupt_debugger(self.process)
        except (OSError, RuntimeError, ValueError) as exc:
            raise GDBError(f"Could not interrupt GDB process: {exc}") from exc
        self.state = "unknown"

    def reconnect(self, elf: str | Path | None = None, extra_args: Sequence[str] = ()) -> None:
        """Restart GDB after a disconnect; the caller must reselect the target."""
        self.close()
        self._records = queue.Queue()
        self.async_records.clear()
        self.stderr_tail.clear()
        self.start(elf, extra_args)

    def close(self) -> None:
        process, self.process = self.process, None
        self.state = "disconnected"
        with self._state_changed:
            self._state_changed.notify_all()
        if process and process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write("-gdb-exit\n")
                    process.stdin.flush()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def __enter__(self) -> MITransport:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
