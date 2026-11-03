"""Portable process, locking, and per-user directory primitives."""

from __future__ import annotations

import getpass
import os
import signal
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import portalocker
import psutil
from platformdirs import user_cache_path, user_state_path


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    created: str
    executable: str
    username: str
    command_line: tuple[str, ...]


class BasePlatform:
    """Shared behavior; OS-specific subclasses override only process controls."""

    name = "unknown"

    @staticmethod
    def create_ipc_server() -> tuple[socket.socket, dict[str, object]]:
        """Create an authenticated endpoint bound only to the local IPv4 loopback."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        server.settimeout(0.25)
        host, port = server.getsockname()[:2]
        return server, {"kind": "tcp", "host": host, "port": port}

    @staticmethod
    def connect_ipc(endpoint: dict[str, object]) -> socket.socket:
        kind = endpoint.get("kind")
        if kind == "tcp":
            host = endpoint.get("host")
            port = endpoint.get("port")
            if host != "127.0.0.1" or not isinstance(port, int):
                raise ValueError("Debug session IPC endpoint must use local loopback TCP")
            return socket.create_connection((host, port))
        if kind == "unix" and hasattr(socket, "AF_UNIX"):
            address = endpoint.get("path")
            if not isinstance(address, str):
                raise ValueError("Debug session Unix socket path is invalid")
            channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            channel.connect(address)
            return channel
        raise ValueError(f"Unsupported debug session IPC endpoint: {kind!r}")

    def state_dir(self) -> Path:
        override = os.environ.get("EDBG_STATE_HOME") or os.environ.get("XDG_STATE_HOME")
        if override:
            return Path(override).expanduser() / "edbg"
        return user_state_path("edbg", appauthor=False)

    def cache_dir(self) -> Path:
        override = os.environ.get("EDBG_CACHE_HOME") or os.environ.get("XDG_CACHE_HOME")
        if override:
            return Path(override).expanduser() / "edbg"
        return user_cache_path("edbg", appauthor=False)

    @contextmanager
    def file_lock(self, path: Path, *, non_blocking: bool = False) -> Iterator[IO[str]]:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+")
        locked = False
        try:
            self.lock_file(handle, non_blocking=non_blocking)
            locked = True
            yield handle
        finally:
            try:
                if locked:
                    self.unlock_file(handle)
            finally:
                handle.close()

    @staticmethod
    def lock_file(handle: IO[str], *, non_blocking: bool = False) -> None:
        flags = portalocker.LOCK_EX | (portalocker.LOCK_NB if non_blocking else 0)
        try:
            portalocker.lock(handle, flags)
        except portalocker.exceptions.LockException as exc:
            raise BlockingIOError(str(exc)) from exc

    @staticmethod
    def unlock_file(handle: IO[str]) -> None:
        portalocker.unlock(handle)

    def process_identity(self, pid: int) -> ProcessIdentity | None:
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                return None
            executable = process.exe()
            username = process.username()
            if not executable or not username:
                return None
            return ProcessIdentity(
                pid=pid,
                created=repr(process.create_time()),
                executable=os.path.normcase(os.path.realpath(executable)),
                username=username,
                command_line=tuple(process.cmdline()),
            )
        except (psutil.Error, OSError, ValueError):
            return None

    def identity_matches(
        self,
        identity: ProcessIdentity | None,
        *,
        pid: int,
        created: object,
        executable: str,
        username: str | None = None,
    ) -> bool:
        if identity is None or identity.pid != pid:
            return False
        expected_exe = os.path.normcase(os.path.realpath(executable))
        return (
            identity.created == str(created)
            and identity.executable == expected_exe
            and (username is None or identity.username.casefold() == username.casefold())
        )

    def legacy_process_identity_matches(
        self, *, pid: int, created: object, executable: str
    ) -> bool | None:
        """Return None when the platform cannot validate a prior state schema."""
        return None

    @staticmethod
    def is_current_user(username: str) -> bool:
        current = getpass.getuser()
        normalized = username.casefold()
        current_normalized = current.casefold()
        return normalized == current_normalized or normalized.endswith("\\" + current_normalized)

    def terminate_process_tree(
        self,
        pid: int,
        timeout: float = 3.0,
        expected_identity: ProcessIdentity | None = None,
    ) -> bool:
        """Terminate this recorded process and its descendants, never a PID alone."""
        try:
            root = psutil.Process(pid)
            if expected_identity is not None:
                current = self.process_identity(pid)
                if current != expected_identity:
                    return False
            children = root.children(recursive=True)
            for process in reversed(children):
                try:
                    process.terminate()
                except psutil.NoSuchProcess:
                    pass
            try:
                root.terminate()
            except psutil.NoSuchProcess:
                pass
            _, alive = psutil.wait_procs([*children, root], timeout=timeout)
            for process in alive:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(alive, timeout=timeout)
            return not alive
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return not psutil.pid_exists(pid)

    def creation_options(self) -> dict[str, Any]:
        return {"start_new_session": os.name == "posix"}

    @staticmethod
    def executable_argv(executable: str, *arguments: str) -> list[str]:
        if Path(executable).suffix.casefold() == ".py":
            return [sys.executable, executable, *arguments]
        return [executable, *arguments]

    def interrupt_debugger(self, process: subprocess.Popen[str]) -> None:
        process.send_signal(signal.SIGINT)

    @staticmethod
    def process_is_running(pid: int) -> bool:
        try:
            return bool(psutil.Process(pid).status() != psutil.STATUS_ZOMBIE)
        except psutil.NoSuchProcess:
            return False
        except psutil.Error:
            return True

    @staticmethod
    def wait_process_exit(pid: int, timeout: float) -> bool:
        try:
            psutil.Process(pid).wait(timeout=timeout)
            return True
        except psutil.TimeoutExpired:
            return False
        except psutil.NoSuchProcess:
            return True
