"""Private, process-identity checked records for the local debug daemon."""

from __future__ import annotations

import json
import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .platforms import get_platform

_SESSION_NAME: ContextVar[str] = ContextVar("embedded_debug_session", default="default")
_SESSION_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def validate_session_name(value: str) -> str:
    if not _SESSION_NAME_PATTERN.fullmatch(value):
        raise ValueError("Session name must be 1-64 letters, digits, dots, underscores, or hyphens")
    return value


def set_session_name(value: str) -> None:
    _SESSION_NAME.set(validate_session_name(value))


def current_session_name() -> str:
    return _SESSION_NAME.get()


def session_dir(name: str | None = None) -> Path:
    selected = validate_session_name(name or current_session_name())
    root = get_platform().state_dir()
    return root if selected == "default" else root / "sessions" / selected


def session_path(name: str | None = None) -> Path:
    return session_dir(name) / "session.json"


def process_start_time(pid: int) -> str | None:
    """Return a cross-platform process creation identity."""
    identity = get_platform().process_identity(pid)
    return identity.created if identity else None


def process_alive(pid: int) -> bool:
    return get_platform().process_is_running(pid)


def _process_identity(
    info: dict[str, Any], pid_key: str, start_key: str, executable_key: str
) -> str:
    """Return match/stale/unknown; unknown must never authorize cleanup or replacement."""
    try:
        pid = int(info[pid_key])
    except (KeyError, TypeError, ValueError):
        return "stale"
    if not process_alive(pid):
        return "stale"
    platform = get_platform()
    identity = platform.process_identity(pid)
    if identity is None:
        return "unknown"
    expected_executable = info.get(executable_key)
    if not isinstance(expected_executable, str) or not expected_executable:
        return "unknown"
    if not platform.is_current_user(identity.username):
        return "stale"
    if identity.created != str(info.get(start_key)) and info.get("schema") == 2:
        legacy_match = platform.legacy_process_identity_matches(
            pid=pid,
            created=info.get(start_key),
            executable=expected_executable,
        )
        return "match" if legacy_match is True else "stale" if legacy_match is False else "unknown"
    username_key = f"{pid_key.removesuffix('_pid')}_username"
    if not platform.identity_matches(
        identity,
        pid=pid,
        created=info.get(start_key),
        executable=expected_executable,
        username=info.get(username_key),
    ):
        return "stale"
    return "match"


def daemon_identity_status(info: dict[str, Any]) -> str:
    status = _process_identity(info, "daemon_pid", "process_start_time", "python_executable")
    if status != "match":
        return status
    identity = get_platform().process_identity(int(info["daemon_pid"]))
    if identity is None:
        return "unknown"
    argv = identity.command_line
    if "embedded_debug.session_daemon" not in argv or str(info.get("session_id")) not in argv:
        return "stale"
    return "match"


def daemon_identity_diagnostic(info: dict[str, Any]) -> str:
    """Explain why an existing daemon record is not trusted during startup."""
    try:
        pid = int(info["daemon_pid"])
    except (KeyError, TypeError, ValueError):
        return "daemon PID is missing or invalid"
    identity = get_platform().process_identity(pid)
    if identity is None:
        return "operating system did not provide process identity"
    expected_created = info.get("process_start_time")
    if identity.created != str(expected_created):
        return "process creation time does not match the session record"
    expected_executable = info.get("python_executable")
    if not isinstance(expected_executable, str) or not expected_executable:
        return "Python executable is missing from the session record"
    if not get_platform().identity_matches(
        identity,
        pid=pid,
        created=expected_created,
        executable=expected_executable,
        username=info.get("daemon_username"),
    ):
        return "process executable or user does not match the session record"
    argv = identity.command_line
    if "embedded_debug.session_daemon" not in argv:
        return "process command line does not identify the EmbeddedDebug daemon"
    if str(info.get("session_id")) not in argv:
        return "process command line has a different session ID"
    return "identity verified"


def daemon_identity_matches(info: dict[str, Any]) -> bool:
    return daemon_identity_status(info) == "match"


def daemon_identity_is_stale(info: dict[str, Any]) -> bool:
    return daemon_identity_status(info) == "stale"


def openocd_identity_matches(info: dict[str, Any]) -> bool:
    return (
        _process_identity(info, "openocd_pid", "openocd_start_time", "openocd_process_executable")
        == "match"
    )


def gdb_identity_matches(info: dict[str, Any]) -> bool:
    # PID, creation time, executable, and user identify the exact child process.
    # Requiring a readable argv is not portable: macOS may omit process arguments.
    return (
        _process_identity(info, "gdb_pid", "gdb_start_time", "gdb_process_executable")
        == "match"
    )


def _terminate_verified_process(
    info: dict[str, Any],
    pid_key: str,
    start_key: str,
    executable_key: str,
    identity_check: Any,
    timeout: float,
) -> bool:
    try:
        pid = int(info[pid_key])
    except (KeyError, TypeError, ValueError):
        return False
    if not identity_check(info):
        return False
    return get_platform().terminate_process_tree(
        pid, timeout, expected_identity=get_platform().process_identity(pid)
    )


def stop_stale_owned_openocd(info: dict[str, Any], timeout: float = 3.0) -> bool:
    """Stop only a child whose UID, PID start time, and executable still match its record."""
    if not info.get("openocd_owned"):
        return False
    return _terminate_verified_process(
        info,
        "openocd_pid",
        "openocd_start_time",
        "openocd_process_executable",
        openocd_identity_matches,
        timeout,
    )


def stop_stale_owned_gdb(info: dict[str, Any], timeout: float = 3.0) -> bool:
    """Stop only the exact GDB/MI child recorded by this session manager."""
    return _terminate_verified_process(
        info,
        "gdb_pid",
        "gdb_start_time",
        "gdb_process_executable",
        gdb_identity_matches,
        timeout,
    )


def read_session(name: str | None = None) -> dict[str, Any] | None:
    try:
        value = json.loads(session_path(name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def save_session(info: dict[str, Any]) -> None:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(info, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def clear_session(session_id: str, name: str | None = None) -> None:
    current = read_session(name)
    if current and current.get("session_id") == session_id:
        session_path(name).unlink(missing_ok=True)


def session_names() -> list[str]:
    root = get_platform().state_dir()
    names = ["default"] if (root / "session.json").is_file() else []
    sessions = root / "sessions"
    if sessions.is_dir():
        names.extend(
            child.name
            for child in sessions.iterdir()
            if child.is_dir()
            and _SESSION_NAME_PATTERN.fullmatch(child.name)
            and (child / "session.json").is_file()
        )
    return sorted(names)


def wait_process_exit(pid: int, timeout: float = 5.0) -> bool:
    return get_platform().wait_process_exit(pid, timeout)
