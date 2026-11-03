"""Per-user debug daemon. It owns one persistent GDB/MI session and optional OpenOCD child."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .gdb.controller import GDBController
from .gdb.mi_transport import GDBError, GDBTimeout, MITransport
from .openocd.manager import OpenOCDError, OpenOCDManager
from .platforms import get_platform
from .session_store import (
    clear_session,
    current_session_name,
    process_start_time,
    read_session,
    save_session,
    session_dir,
    set_session_name,
)


class SessionFailure(RuntimeError):
    pass


class DebugSessionDaemon:
    def __init__(self, session_id: str, settings: dict[str, Any]) -> None:
        self.session_id = session_id
        self.settings = settings
        self.lock_handle: Any = None
        self.lock_acquired = False
        self.server: socket.socket | None = None
        self.ipc_endpoint: dict[str, object] | None = None
        self.stopping = threading.Event()
        self._operation_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._client_threads: set[threading.Thread] = set()
        self.manager = OpenOCDManager(
            settings["openocd_executable"],
            settings["host"],
            int(settings["gdb_port"]),
            Path(settings["log_file"]),
        )
        self.transport = MITransport(
            settings["gdb_executable"], float(settings.get("gdb_timeout", 10.0))
        )
        self.controller = GDBController(self.transport)
        self.target: dict[str, Any] = {}
        self.capabilities: dict[str, str] = {}

    def start(self) -> None:
        root = session_dir()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        lock_path = root / "manager.lock"
        self.lock_handle = lock_path.open("a+")
        try:
            get_platform().lock_file(self.lock_handle, non_blocking=True)
            self.lock_acquired = True
            print("session daemon lock acquired", file=sys.stderr, flush=True)
        except (BlockingIOError, OSError) as exc:
            raise SessionFailure(
                "Another debug session manager already owns this user session"
            ) from exc
        self.manager.start(
            self.settings.get("openocd_config"),
            self.settings.get("interface"),
            self.settings.get("target_config"),
            self.settings.get("board"),
            self.settings.get("commands", []),
            self.settings.get("adapter_khz"),
            config_files=self.settings.get("config_files", []),
            extra_args=self.settings.get("server_args", []),
        )
        print("session daemon OpenOCD ready", file=sys.stderr, flush=True)
        if self.stopping.is_set():
            raise SessionFailure("Session shutdown requested during OpenOCD startup")
        self._connect_gdb()
        print("session daemon GDB connected", file=sys.stderr, flush=True)
        if self.stopping.is_set():
            raise SessionFailure("Session shutdown requested during GDB startup")
        server, endpoint = get_platform().create_ipc_server()
        self.server = server
        self.ipc_endpoint = endpoint
        print("session daemon IPC ready", file=sys.stderr, flush=True)
        save_session(
            {
                "schema": 3,
                "session_id": self.session_id,
                "session_name": current_session_name(),
                "token": self.settings["token"],
                "daemon_pid": os.getpid(),
                "process_start_time": process_start_time(os.getpid()),
                "ipc_endpoint": endpoint,
                "python_executable": str(Path(sys.executable).resolve()),
                "host": self.settings["host"],
                "gdb_port": int(self.settings["gdb_port"]),
                "gdb_executable": self.settings["gdb_executable"],
                "gdb_timeout": float(self.settings.get("gdb_timeout", 10.0)),
                "elf": self.settings.get("elf"),
                "openocd_executable": self.settings["openocd_executable"],
                "openocd_config": self.settings.get("openocd_config"),
                "interface": self.settings.get("interface"),
                "target_config": self.settings.get("target_config"),
                "board": self.settings.get("board"),
                "config_files": self.settings.get("config_files", []),
                "server_args": self.settings.get("server_args", []),
                "commands": self.settings.get("commands", []),
                "adapter_khz": self.settings.get("adapter_khz"),
                "openocd_pid": self.manager.process.pid if self.manager.process else None,
                "openocd_process_executable": self.manager.process_executable,
                "openocd_start_time": (
                    process_start_time(self.manager.process.pid) if self.manager.process else None
                ),
                "openocd_owned": self.manager.process is not None,
                "gdb_pid": self.transport.process.pid if self.transport.process else None,
                "gdb_process_executable": self.transport.process_executable,
                "gdb_start_time": (
                    process_start_time(self.transport.process.pid)
                    if self.transport.process
                    else None
                ),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    def _connect_gdb(self) -> None:
        self.controller = GDBController(self.transport)
        try:
            result = self.controller.connect(
                self.settings["host"],
                int(self.settings["gdb_port"]),
                self.settings.get("elf"),
            )
        except (GDBError, GDBTimeout) as exc:
            tail = "\n".join(self.transport.stderr_tail)
            detail = f"; GDB stderr: {tail}" if tail else ""
            raise SessionFailure(
                f"Could not validate the GDB server/target: {exc}{detail}"
            ) from exc
        self.target = {
            "architecture": result.get("architecture"),
            "connection": "connected",
        }
        self.capabilities = result.get("capabilities", {})

    def serve(self) -> None:
        if self.server is None:
            raise SessionFailure("Debug IPC server was not initialized")
        while not self.stopping.is_set():
            try:
                connection, _ = self.server.accept()
            except TimeoutError:
                if self.transport.process and self.transport.process.poll() is not None:
                    self.transport.state = "disconnected"
                if self.manager.process and self.manager.process.poll() is not None:
                    self.transport.state = "disconnected"
                continue
            worker = threading.Thread(
                target=self._handle_connection, args=(connection,), daemon=True
            )
            with self._client_lock:
                self._client_threads.add(worker)
            worker.start()

    def _handle_connection(self, connection: socket.socket) -> None:
        try:
            with connection:
                connection.settimeout(150.0)
                self._serve_connection(connection)
        finally:
            with self._client_lock:
                self._client_threads.discard(threading.current_thread())

    def _serve_connection(self, connection: socket.socket) -> None:
        stream = connection.makefile("rb")
        line = stream.readline(1_048_577)
        if not line or len(line) > 1_048_576:
            self._reply(connection, {"success": False, "error": "Invalid or oversized request"})
            return
        try:
            request = json.loads(line)
            if (
                request.get("session_id") != self.session_id
                or request.get("token") != self.settings["token"]
            ):
                raise SessionFailure("IPC authentication failed")
            operation = request.get("operation")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise SessionFailure("IPC params must be an object")
            if operation == "cancel":
                response = self._dispatch(str(operation), params)
            else:
                with self._operation_lock:
                    response = self._dispatch(str(operation), params)
        except (
            ValueError,
            TypeError,
            KeyError,
            PermissionError,
            GDBError,
            GDBTimeout,
            OpenOCDError,
            SessionFailure,
        ) as exc:
            response = {"success": False, "error": str(exc)}
        except Exception as exc:  # Keep one malformed operation from killing the owner process.
            response = {"success": False, "error": f"Internal session error: {exc}"}
        self._reply(connection, response)

    @staticmethod
    def _reply(connection: socket.socket, response: dict[str, Any]) -> None:
        try:
            connection.sendall(json.dumps(response, default=str).encode() + b"\n")
        except OSError:
            pass

    def _require_halted(self) -> None:
        if self.transport.state != "halted":
            raise SessionFailure(
                f"Target state is {self.transport.state}; halt the target before this inspection"
            )

    def _recover(self, *, authorize_openocd_restart: bool) -> dict[str, Any]:
        self.transport.close()
        if self.manager.process is None or self.manager.process.poll() is not None:
            if (
                not OpenOCDManager.port_open(self.settings["host"], int(self.settings["gdb_port"]))
                and not authorize_openocd_restart
            ):
                raise PermissionError(
                    "OpenOCD is unavailable; pass --authorize-openocd-restart before "
                    "starting the configured server"
                )
            if not OpenOCDManager.port_open(
                self.settings["host"], int(self.settings["gdb_port"])
            ) and not any(
                (
                    self.settings.get("openocd_config"),
                    self.settings.get("interface"),
                    self.settings.get("target_config"),
                    self.settings.get("board"),
                    self.settings.get("config_files"),
                    self.settings.get("server_args"),
                    self.settings.get("commands"),
                )
            ):
                raise SessionFailure(
                    "No saved OpenOCD configuration is available for recovery; "
                    "provide the project's existing configuration"
                )
            self.manager.start(
                self.settings.get("openocd_config"),
                self.settings.get("interface"),
                self.settings.get("target_config"),
                self.settings.get("board"),
                self.settings.get("commands", []),
                self.settings.get("adapter_khz"),
                config_files=self.settings.get("config_files", []),
                extra_args=self.settings.get("server_args", []),
            )
        self.transport = MITransport(
            self.settings["gdb_executable"], float(self.settings.get("gdb_timeout", 10.0))
        )
        self._connect_gdb()
        record = read_session()
        if record and record.get("session_id") == self.session_id:
            record.update(
                {
                    "openocd_pid": self.manager.process.pid if self.manager.process else None,
                    "openocd_process_executable": self.manager.process_executable,
                    "openocd_start_time": (
                        process_start_time(self.manager.process.pid)
                        if self.manager.process
                        else None
                    ),
                    "openocd_owned": self.manager.process is not None,
                    "gdb_pid": self.transport.process.pid if self.transport.process else None,
                    "gdb_process_executable": self.transport.process_executable,
                    "gdb_start_time": (
                        process_start_time(self.transport.process.pid)
                        if self.transport.process
                        else None
                    ),
                }
            )
            save_session(record)
        return {"recovered": True, "target": self.target}

    def _dispatch(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.manager.process and self.manager.process.poll() is not None:
            self.transport.state = "disconnected"
        if self.transport.process and self.transport.process.poll() is not None:
            self.transport.state = "disconnected"
        if operation == "status":
            link_state = "unknown"
            link_error: str | None = None
            if self.transport.state == "halted" and self.transport.process:
                try:
                    self.transport.command(
                        "-data-list-register-values x",
                        timeout=min(2.0, float(self.settings.get("gdb_timeout", 10.0))),
                    )
                    link_state = "verified"
                except GDBTimeout as exc:
                    self.transport.state = "unknown"
                    link_state = "unknown"
                    link_error = str(exc)
                except GDBError as exc:
                    link_error = str(exc)
                    if self._is_link_failure(exc):
                        self.transport.state = "disconnected"
                        link_state = "disconnected"
                    else:
                        self.transport.state = "unknown"
                        link_state = "unknown"
            elif self.transport.state == "running":
                link_state = "unverified_running"
            connected = (
                bool(self.transport.process and self.transport.process.poll() is None)
                and self.transport.state != "disconnected"
            )
            return {
                "success": True,
                "data": {
                    "active": connected,
                    "session_name": current_session_name(),
                    "target_link": link_state,
                    "target_link_error": link_error,
                    "target": self.target,
                    "target_state": self.transport.state,
                    "host": self.settings["host"],
                    "gdb_port": int(self.settings["gdb_port"]),
                    "gdb_executable": self.settings["gdb_executable"],
                    "gdb_timeout": float(self.settings.get("gdb_timeout", 10.0)),
                    "elf": self.settings.get("elf"),
                    "openocd_executable": self.settings["openocd_executable"],
                    "openocd_config": self.settings.get("openocd_config"),
                    "interface": self.settings.get("interface"),
                    "target_config": self.settings.get("target_config"),
                    "board": self.settings.get("board"),
                    "config_files": self.settings.get("config_files", []),
                    "server_args": self.settings.get("server_args", []),
                    "commands": self.settings.get("commands", []),
                    "adapter_khz": self.settings.get("adapter_khz"),
                    "capabilities": self.capabilities,
                    "openocd_owned": self.manager.process is not None,
                    "gdb_pid": self.transport.process.pid if self.transport.process else None,
                    "async_events": [
                        record.raw for record in list(self.transport.async_records)[-50:]
                    ],
                },
            }
        if operation == "recover":
            recovery_data = self._recover(
                authorize_openocd_restart=bool(params.get("authorize_openocd_restart"))
            )
            return {
                "success": True,
                "data": recovery_data,
                "target_state": self.transport.state,
            }
        if operation == "stop":
            self.stopping.set()
            return {
                "success": True,
                "data": {"stopping": True},
                "target_state": self.transport.state,
            }
        if operation == "cancel":
            if not params.get("authorized_run"):
                raise PermissionError(
                    "Cancelling a GDB operation requires explicit --authorize-run"
                )
            self.transport.cancel()
            return {
                "success": True,
                "data": {"cancel_requested": True},
                "target": self.target,
                "target_state": self.transport.state,
            }
        if operation in {"continue", "step", "next"} and not params.get("authorized_run"):
            raise PermissionError("Target execution requires explicit --authorize-run")
        if operation in {"reset", "memory-write", "flash"} and not params.get("authorized"):
            raise PermissionError(f"{operation} requires explicit --authorize")
        if operation == "halt":
            self.controller.halt()
            if not self.transport.wait_for_state("halted", 3.0):
                raise GDBTimeout("GDB accepted halt but no asynchronous stop event arrived")
            data: Any = {"state": self.transport.state}
        elif operation == "continue":
            self._require_halted()
            self.controller.resume()
            data = {"state": self.transport.state}
        elif operation in {"step", "next"}:
            self._require_halted()
            self.transport.command("-exec-step" if operation == "step" else "-exec-next")
            data = {"state": self.transport.state}
        elif operation == "reset":
            self.transport.command('-interpreter-exec console "monitor reset halt"')
            if not self.transport.wait_for_state("halted", 5.0):
                raise GDBTimeout("Reset completed without a confirmed halted event")
            data = {"state": self.transport.state}
        elif operation == "registers":
            self._require_halted()
            data = self.controller.registers_read()
        elif operation == "variable-read":
            self._require_halted()
            expression = str(params["expression"])
            data = {"expression": expression, "value": self.controller.variable_read(expression)}
        elif operation == "backtrace":
            self._require_halted()
            data = self.controller.backtrace()
        elif operation == "breakpoint-add":
            self._require_halted()
            data = self.controller.breakpoint_add(str(params["location"]))
        elif operation == "breakpoint-list":
            data = self.controller.breakpoint_list()
        elif operation == "breakpoint-remove":
            self._require_halted()
            data = self.transport.command(f"-break-delete {int(params['number'])}").results
        elif operation == "memory-read":
            self._require_halted()
            address, length = int(params["address"]), int(params["length"])
            if address < 0 or not 1 <= length <= 4096:
                raise ValueError("memory read requires a nonnegative address and length 1..4096")
            data = self.transport.command(f"-data-read-memory-bytes 0x{address:x} {length}").results
        elif operation == "memory-write":
            self._require_halted()
            address, length, value = (
                int(params["address"]),
                int(params["length"]),
                int(params["value"]),
            )
            if address < 0 or not 1 <= length <= 8 or not 0 <= value < 1 << (8 * length):
                raise ValueError(
                    "memory write requires a nonnegative address and fitting 1..8 byte value"
                )
            payload = value.to_bytes(length, "little").hex()
            data = self.transport.command(
                f'-data-write-memory-bytes 0x{address:x} "{payload}"'
            ).results
        elif operation == "threads":
            data = {
                "gdb_threads": self.transport.command("-thread-info").results,
                "rtos_awareness": self.capabilities.get("rtos_awareness", "unknown"),
            }
        elif operation == "thread-inspect":
            thread_info = self.transport.command("-thread-info").results or {}
            threads = thread_info.get("threads", [])
            requested = str(params["thread_id"])
            data = next((thread for thread in threads if str(thread.get("id")) == requested), None)
            if data is None:
                raise ValueError(f"GDB thread {requested} was not reported")
        elif operation == "thread-select":
            self._require_halted()
            data = self.controller.thread_select(str(params["thread_id"]))
        elif operation == "snapshot":
            self._require_halted()
            data = {
                "registers": self.controller.registers_read(),
                "backtrace": self.controller.backtrace(),
                "architecture": self.controller.architecture,
            }
        elif operation == "fault-analyze":
            raise SessionFailure("No architecture-specific fault diagnostic plugin is available")
        elif operation == "flash":
            self._require_halted()
            if not self.settings.get("elf"):
                raise ValueError("flash requires a selected ELF image")
            data = self.transport.command('-interpreter-exec console "load"', timeout=120).results
        else:
            raise ValueError(f"Unsupported debug session operation: {operation}")
        return {
            "success": True,
            "data": data,
            "target": self.target,
            "target_state": self.transport.state,
            "capabilities": self.capabilities,
        }

    def _is_link_failure(self, error: GDBError) -> bool:
        if not self.transport.process or self.transport.process.poll() is not None:
            return True
        message = str(error).lower()
        return any(
            phrase in message
            for phrase in (
                "remote connection closed",
                "connection reset by peer",
                "connection refused",
                "remote failure reply",
                "target disconnected",
                "target is not responding",
                "not connected",
            )
        )

    def close(self) -> None:
        self.stopping.set()
        if self.server:
            self.server.close()
            self.server = None
        with self._client_lock:
            clients = list(self._client_threads)
        for worker in clients:
            if worker is not threading.current_thread():
                worker.join(timeout=1.0)
        self.transport.close()
        self.manager.stop()
        clear_session(self.session_id)
        if self.lock_handle and self.lock_acquired:
            get_platform().unlock_file(self.lock_handle)
            self.lock_acquired = False
        if self.lock_handle:
            self.lock_handle.close()
            self.lock_handle = None


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--session-name", default="default")
    parser.add_argument("--settings", type=Path, required=True)
    args = parser.parse_args()
    set_session_name(args.session_name)
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    print("session daemon settings loaded", file=sys.stderr, flush=True)
    daemon = DebugSessionDaemon(args.session_id, settings)

    def stop_handler(_signum: int, _frame: Any) -> None:
        daemon.stopping.set()
        daemon.transport.close()
        daemon.manager.stop()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        daemon.start()
        daemon.serve()
        return 0
    except Exception as exc:
        log = Path(settings.get("log_file", session_dir() / "manager.log"))
        try:
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"session manager startup/runtime error: {exc}\n")
        except OSError:
            pass
        return 1
    finally:
        daemon.close()
        args.settings.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(_main())
