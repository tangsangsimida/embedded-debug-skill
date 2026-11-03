"""Private, process-identity checked records for the local debug daemon."""

from __future__ import annotations

import json
import os
import select
import signal
import time
from pathlib import Path
from typing import Any


def session_dir() -> Path:
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return state_home / "edbg"


def session_path() -> Path:
    return session_dir() / "session.json"


def process_start_time(pid: int) -> str | None:
    """Return Linux /proc start ticks, resilient to spaces in the process name."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields_after_comm = stat[stat.rfind(")") + 2 :].split()
        return fields_after_comm[19]  # field 22 (starttime), field 3 is index zero
    except (OSError, IndexError):
        return None


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    state_path = Path(f"/proc/{pid}/stat")
    if state_path.is_file():
        try:
            stat = state_path.read_text(encoding="utf-8")
            return stat[stat.rfind(")") + 2 :].split()[0] != "Z"
        except (OSError, IndexError):
            return False
    return True


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
    start_time = process_start_time(pid)
    if start_time is None:
        return "unknown"
    if start_time != str(info.get(start_key)):
        return "stale"
    try:
        uid_line = next(
            line
            for line in Path(f"/proc/{pid}/status").read_text().splitlines()
            if line.startswith("Uid:")
        )
        if int(uid_line.split()[1]) != os.getuid():
            return "stale"
        actual_executable = str(Path(f"/proc/{pid}/exe").resolve(strict=True))
    except (OSError, StopIteration, ValueError, IndexError):
        return "unknown"
    expected_executable = info.get(executable_key)
    if not isinstance(expected_executable, str) or not expected_executable:
        return "unknown"
    try:
        if actual_executable != str(Path(expected_executable).resolve(strict=True)):
            return "stale"
    except OSError:
        return "unknown"
    return "match"


def daemon_identity_status(info: dict[str, Any]) -> str:
    status = _process_identity(
        info, "daemon_pid", "process_start_time", "python_executable"
    )
    if status != "match":
        return status
    pid = int(info["daemon_pid"])
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return "unknown"
    if "embedded_debug.session_daemon" not in argv or str(info.get("session_id")) not in argv:
        return "stale"
    return "match"


def daemon_identity_matches(info: dict[str, Any]) -> bool:
    return daemon_identity_status(info) == "match"


def daemon_identity_is_stale(info: dict[str, Any]) -> bool:
    return daemon_identity_status(info) == "stale"


def openocd_identity_matches(info: dict[str, Any]) -> bool:
    return _process_identity(
        info, "openocd_pid", "openocd_start_time", "openocd_process_executable"
    ) == "match"


def gdb_identity_matches(info: dict[str, Any]) -> bool:
    if _process_identity(info, "gdb_pid", "gdb_start_time", "gdb_process_executable") != "match":
        return False
    try:
        argv = Path(f"/proc/{int(info['gdb_pid'])}/cmdline").read_bytes().decode().split("\0")
    except (OSError, TypeError, ValueError, KeyError):
        return False
    return "--interpreter=mi2" in argv


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
    pidfd: int | None = None
    try:
        if hasattr(os, "pidfd_open"):
            pidfd = os.pidfd_open(pid)
        if not identity_check(info):
            return False
        if pidfd is not None and hasattr(signal, "pidfd_send_signal"):
            signal.pidfd_send_signal(pidfd, signal.SIGTERM)
            readable, _, _ = select.select([pidfd], [], [], timeout)
            if readable:
                return True
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            readable, _, _ = select.select([pidfd], [], [], timeout)
            return bool(readable)
        os.kill(pid, signal.SIGTERM)
        if wait_process_exit(pid, timeout):
            return True
        if _process_identity(info, pid_key, start_key, executable_key) != "match":
            return False
        os.kill(pid, signal.SIGKILL)
        return wait_process_exit(pid, timeout)
    except (OSError, ProcessLookupError):
        return not process_alive(pid)
    finally:
        if pidfd is not None:
            os.close(pidfd)


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


def read_session() -> dict[str, Any] | None:
    try:
        value = json.loads(session_path().read_text(encoding="utf-8"))
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
    path.chmod(0o600)


def clear_session(session_id: str) -> None:
    current = read_session()
    if current and current.get("session_id") == session_id:
        session_path().unlink(missing_ok=True)


def wait_process_exit(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.05)
    return not process_alive(pid)
