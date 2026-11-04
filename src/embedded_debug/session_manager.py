"""Start, verify, and control the per-user persistent debug daemon."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from typing import Any

from .platforms import get_platform
from .session_ipc import SessionUnavailable, request_session
from .session_store import (
    current_session_name,
    daemon_identity_diagnostic,
    daemon_identity_is_stale,
    daemon_identity_matches,
    daemon_identity_status,
    read_session,
    session_dir,
    session_names,
    session_path,
    stop_stale_owned_gdb,
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
    try:
        root.chmod(0o700)
    except OSError:
        pass
    current = _current_status()
    if current:
        _ensure_compatible(settings, current["data"])
        return {**current["data"], "reused_session": True}

    lock_path = get_platform().state_dir() / "session-registry.lock"
    with get_platform().file_lock(lock_path):
        current = _current_status()
        if current:
            _ensure_compatible(settings, current["data"])
            return {**current["data"], "reused_session": True}
        old = read_session()
        if old and daemon_identity_matches(old):
            raise SessionManagerError(
                "A live session manager could not answer IPC; refusing to start a competing GDB"
            )
        if old and not daemon_identity_is_stale(old):
            raise SessionManagerError(
                "A previous debug session identity cannot be verified; refusing to start "
                "a competing GDB/OpenOCD session"
            )
        if old:
            stop_stale_owned_gdb(old)
            stop_stale_owned_openocd(old)
            current_record = read_session()
            if current_record and current_record.get("session_id") == old.get("session_id"):
                session_path().unlink(missing_ok=True)
        _ensure_endpoint_unique(settings)
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
            log_handle.write(f"launching session daemon {session_id}\n")
            log_handle.flush()
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "embedded_debug.session_daemon",
                    "--session-id",
                    session_id,
                    "--session-name",
                    current_session_name(),
                    "--settings",
                    str(settings_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                close_fds=True,
                **get_platform().creation_options(),
            )
            log_handle.write(f"session daemon process started: pid={process.pid}\n")
            log_handle.flush()
        finally:
            log_handle.close()
        deadline = time.monotonic() + timeout
        identity_diagnostic = "session record not written yet"
        while time.monotonic() < deadline:
            info = read_session()
            identity_status = "missing"
            if info and info.get("session_id") == session_id:
                identity_status = daemon_identity_status(info)
                if identity_status != "match":
                    identity_diagnostic = daemon_identity_diagnostic(info)
                if identity_status == "match":
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
        get_platform().terminate_process_tree(process.pid, timeout=7)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        settings_path.unlink(missing_ok=True)
        details = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        suffix = f" Diagnostics: {details.strip()}" if details.strip() else ""
        raise SessionManagerError(
            f"Debug session manager did not become ready within {timeout}s "
            f"(session record identity: {identity_status}; {identity_diagnostic}).{suffix}"
        )


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


def _ensure_endpoint_unique(settings: dict[str, Any]) -> None:
    selected = current_session_name()
    loopback = {"127.0.0.1", "localhost", "::1"}
    for name in session_names():
        if name == selected:
            continue
        existing = read_session(name)
        if not existing:
            continue
        active = daemon_identity_matches(existing)
        stale = daemon_identity_is_stale(existing)
        same_loopback_port = (
            existing.get("host") in loopback
            and settings.get("host") in loopback
            and existing.get("gdb_port") == settings.get("gdb_port")
        )
        if active and same_loopback_port:
            raise SessionManagerError(
                f"GDB endpoint {settings.get('host')}:{settings.get('gdb_port')} is already "
                f"owned by session {name!r}; select a different target or stop that session"
            )
        if not active and not stale and same_loopback_port:
            raise SessionManagerError(
                f"GDB endpoint {settings.get('host')}:{settings.get('gdb_port')} has an "
                f"unverifiable session record {name!r}; refusing to compete for it"
            )
        if stale:
            stop_stale_owned_gdb(existing)
            stop_stale_owned_openocd(existing)
            current = read_session(name)
            if current and current.get("session_id") == existing.get("session_id"):
                session_path(name).unlink(missing_ok=True)


def list_sessions() -> list[dict[str, Any]]:
    result = []
    for name in session_names():
        info = read_session(name)
        if not info:
            continue
        active = daemon_identity_matches(info)
        result.append(
            {
                "name": name,
                "active": active,
                "session_id": info.get("session_id"),
                "target_state": "unknown" if active else "disconnected",
                "host": info.get("host"),
                "gdb_port": info.get("gdb_port"),
            }
        )
    return result


def session_status() -> dict[str, Any]:
    info = read_session()
    if not info or not daemon_identity_matches(info):
        if info and daemon_identity_is_stale(info):
            stop_stale_owned_gdb(info)
            stop_stale_owned_openocd(info)
            current = read_session()
            if current and current.get("session_id") == info.get("session_id"):
                session_path().unlink(missing_ok=True)
        elif info:
            return {
                "active": False,
                "target_state": "unknown",
                "error": "Session manager identity is unavailable; refusing to replace it",
            }
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
        if info and daemon_identity_is_stale(info):
            stop_stale_owned_gdb(info)
            stop_stale_owned_openocd(info)
            current = read_session()
            if current and current.get("session_id") == info.get("session_id"):
                session_path().unlink(missing_ok=True)
        elif info:
            raise SessionManagerError(
                "Session manager identity is unavailable; refusing to stop or replace processes"
            )
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
