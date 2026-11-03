"""Local-only OpenOCD process lifecycle management."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO


class OpenOCDError(RuntimeError):
    pass


class OpenOCDManager:
    def __init__(
        self,
        executable: str = "openocd",
        host: str = "127.0.0.1",
        port: int = 3333,
        log_file: Path | None = None,
    ) -> None:
        self.executable, self.host, self.port = executable, host, port
        self.log_file = log_file
        self.process: subprocess.Popen[str] | None = None
        self.process_executable: str | None = None
        self._log_handle: TextIO | None = None

    @staticmethod
    def port_open(host: str, port: int, timeout: float = 0.2) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def start(
        self,
        config: str | Path | None = None,
        interface: str | None = None,
        target: str | None = None,
        board: str | None = None,
        commands: Sequence[str] = (),
        adapter_khz: int | None = None,
        timeout: float = 10.0,
        config_files: Sequence[str] = (),
        extra_args: Sequence[str] = (),
    ) -> None:
        if self.port_open(self.host, self.port):
            # A previous owned child may have exited while another GDB server
            # took over its port. Do not retain the stale process as our child.
            if self.process is not None and self.process.poll() is not None:
                self.process = None
                self.process_executable = None
                if self._log_handle:
                    self._log_handle.close()
                    self._log_handle = None
            return  # Reuse an existing service; do not spawn a competing server.
        exe = shutil.which(self.executable) or (
            self.executable if Path(self.executable).is_file() else None
        )
        if not exe:
            raise OpenOCDError(f"OpenOCD executable not found: {self.executable}")
        args = [exe]
        if config:
            args += ["-f", str(config)]
        for config_file in config_files:
            args += ["-f", config_file]
        # Apply the loopback bind after user config so a config cannot accidentally
        # expose the GDB/Telnet/Tcl services on external interfaces.
        for item in (interface, target, board):
            if item:
                args += ["-f", item]
        args.extend(extra_args)
        user_commands = list(commands)
        for index, value in enumerate(extra_args[:-1]):
            if value == "-c":
                user_commands.append(extra_args[index + 1])
        for command in user_commands:
            parts = command.strip().split()
            if (
                parts
                and parts[0] == "bindto"
                and (len(parts) != 2 or parts[1] not in {"127.0.0.1", "localhost", "::1"})
            ):
                raise OpenOCDError("OpenOCD bindto commands must remain on loopback")
        # Apply configured service ports after target config scripts.
        args += ["-c", f"gdb_port {self.port}"]
        # Apply after user config, before init/adapter commands, to keep listeners local.
        args += ["-c", f"bindto {self.host}"]
        if adapter_khz:
            args += ["-c", f"adapter speed {adapter_khz}"]
        for command in commands:
            args += ["-c", command]
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log_file.open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            args, stdout=self._log_handle or subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True
        )
        proc_executable = Path(f"/proc/{self.process.pid}/exe")
        try:
            self.process_executable = str(proc_executable.resolve(strict=True))
        except OSError:
            self.process_executable = str(Path(exe).resolve())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise OpenOCDError(
                    f"OpenOCD exited with status {self.process.returncode}; "
                    f"see {self.log_file or 'process output'}"
                )
            if self.port_open(self.host, self.port):
                return
            time.sleep(0.1)
        self.stop()
        raise OpenOCDError(
            f"OpenOCD did not open GDB port {self.host}:{self.port} within {timeout}s"
        )

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        self.process_executable = None
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

    def __enter__(self) -> OpenOCDManager:
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
