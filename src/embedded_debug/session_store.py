"""Private, process-identity checked records for the local debug daemon."""

from __future__ import annotations

import json
import os
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


def daemon_identity_matches(info: dict[str, Any]) -> bool:
    try:
        pid = int(info["daemon_pid"])
    except (KeyError, TypeError, ValueError):
        return False
    if not process_alive(pid) or process_start_time(pid) != str(info.get("process_start_time")):
        return False
    status_path = Path(f"/proc/{pid}/status")
    try:
        uid_line = next(
            line for line in status_path.read_text().splitlines() if line.startswith("Uid:")
        )
        if int(uid_line.split()[1]) != os.getuid():
            return False
    except (OSError, StopIteration, ValueError, IndexError):
        return False
    command_path = Path(f"/proc/{pid}/cmdline")
    if not command_path.is_file():
        return False
    try:
        argv = command_path.read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return False
    return "embedded_debug.session_daemon" in argv and str(info.get("session_id")) in argv


def openocd_identity_matches(info: dict[str, Any]) -> bool:
    try:
        pid = int(info["openocd_pid"])
    except (KeyError, TypeError, ValueError):
        return False
    if not process_alive(pid) or process_start_time(pid) != str(info.get("openocd_start_time")):
        return False
    try:
        uid_line = next(
            line
            for line in Path(f"/proc/{pid}/status").read_text().splitlines()
            if line.startswith("Uid:")
        )
        if int(uid_line.split()[1]) != os.getuid():
            return False
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except (OSError, StopIteration, ValueError, IndexError):
        return False
    executable = Path(str(info.get("openocd_executable", "openocd"))).name
    return bool(executable and executable in command)


def stop_stale_owned_openocd(info: dict[str, Any], timeout: float = 3.0) -> bool:
    """Stop only a child whose UID, PID start time, and executable still match its record."""
    if not info.get("openocd_owned") or not openocd_identity_matches(info):
        return False
    pid = int(info["openocd_pid"])
    os.kill(pid, signal.SIGTERM)
    return wait_process_exit(pid, timeout)


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
