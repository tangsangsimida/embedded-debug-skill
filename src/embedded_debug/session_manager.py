"""Start, verify, and control the per-user persistent debug daemon."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from typing import Any

from .session_ipc import SessionUnavailable, request_session
from .session_store import (
    daemon_identity_matches,
    read_session,
    session_dir,
    session_path,
    stop_stale_owned_openocd,
)


class SessionManagerError(RuntimeError):
    pass


def _current_status() -> dict[str, Any] | None:
    info = read_session()
    if not info:
        return None
    if not daemon_identity_matches(info):
        return None
    response = request_session("status")
    if not response.get("success"):
        raise SessionManagerError(str(response.get("error", "Debug session status failed")))
    return response


def start_session(settings: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    root = session_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    current = _current_status()
    if current:
        _ensure_compatible(settings, current["data"])
        return {**current["data"], "reused_session": True}

    lock_path = root / "start.lock"
    with lock_path.open("a+") as start_lock:
        fcntl.flock(start_lock.fileno(), fcntl.LOCK_EX)
        current = _current_status()
        if current:
            _ensure_compatible(settings, current["data"])
            return {**current["data"], "reused_session": True}
        old = read_session()
        if old and daemon_identity_matches(old):
            raise SessionManagerError(
                "A live session manager could not answer IPC; refusing to start a competing GDB"
            )
        if old:
            stop_stale_owned_openocd(old)
            session_path().unlink(missing_ok=True)
        session_id = uuid.uuid4().hex
        runtime_settings = dict(settings)
        runtime_settings["token"] = secrets.token_urlsafe(32)
        settings_path = root / f"settings-{session_id}.json"
        log_path = root / "manager.log"
        runtime_settings["log_file"] = str(log_path)
        descriptor = os.open(settings_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(runtime_settings, handle)
            handle.flush()
            os.fsync(handle.fileno())
        log_handle = log_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "embedded_debug.session_daemon",
                    "--session-id",
                    session_id,
                    "--settings",
                    str(settings_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log_handle.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = read_session()
            if info and info.get("session_id") == session_id:
                try:
                    status = request_session("status")
                except SessionUnavailable:
                    status = None
                if status and status.get("success"):
                    return {**status["data"], "reused_session": False}
            return_code = process.poll()
            if return_code is not None:
                details = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                settings_path.unlink(missing_ok=True)
                raise SessionManagerError(
                    f"Debug session manager exited with status {return_code}. {details.strip()}"
                )
            time.sleep(0.05)
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        settings_path.unlink(missing_ok=True)
        raise SessionManagerError(f"Debug session manager did not become ready within {timeout}s")


def _ensure_compatible(settings: dict[str, Any], current: dict[str, Any]) -> None:
    keys = (
        "host",
        "gdb_port",
        "gdb_executable",
        "gdb_timeout",
        "elf",
        "openocd_executable",
        "openocd_config",
        "interface",
        "target_config",
        "board",
        "config_files",
        "server_args",
        "commands",
        "adapter_khz",
    )
    for key in keys:
        desired = settings.get(key)
        if key == "elf" and desired is not None:
            desired = str(desired)
        if current.get(key) != desired:
            raise SessionManagerError(
                f"A different debug session is already active ({key}="
                f"{current.get(key)!r}); stop it before selecting another target"
            )


def session_status() -> dict[str, Any]:
    info = read_session()
    if not info or not daemon_identity_matches(info):
        if info:
            session_path().unlink(missing_ok=True)
        return {"active": False, "target_state": "disconnected"}
    try:
        response = request_session("status")
    except SessionUnavailable as exc:
        return {
            "active": False,
            "target_state": "unknown",
            "error": str(exc),
            "manager_pid": info.get("daemon_pid"),
        }
    if not response.get("success"):
        return {"active": False, "target_state": "unknown", "error": response.get("error")}
    data = response.get("data")
    if not isinstance(data, dict):
        return {"active": False, "target_state": "unknown", "error": "Invalid status response"}
    return data


def stop_session(timeout: float = 6.0) -> dict[str, Any]:
    info = read_session()
    if not info or not daemon_identity_matches(info):
        if info:
            session_path().unlink(missing_ok=True)
        return {"stopped": False, "reason": "no verified session manager"}
    try:
        response = request_session("stop")
    except SessionUnavailable as exc:
        raise SessionManagerError(f"Cannot safely stop session manager: {exc}") from exc
    if not response.get("success"):
        raise SessionManagerError(str(response.get("error", "Session stop was rejected")))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not daemon_identity_matches(info):
            return {"stopped": True, "manager_pid": info["daemon_pid"]}
        time.sleep(0.05)
    raise SessionManagerError("Session manager did not exit after its stop request")


def session_operation(operation: str, **params: Any) -> dict[str, Any]:
    response = request_session(operation, **params)
    if not response.get("success"):
        raise SessionManagerError(str(response.get("error", "Debug operation failed")))
    return response
