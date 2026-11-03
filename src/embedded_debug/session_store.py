"""Small ownership record for OpenOCD processes started by the CLI."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from typing import Any


def session_path() -> Path:
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return state_home / "edbg" / "session.json"


def read_session() -> dict[str, Any] | None:
    path = session_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def save_session(info: dict[str, Any]) -> None:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(info, indent=2), encoding="utf-8")
    temp.replace(path)


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    if stat.is_file():
        try:
            if stat.read_text(encoding="utf-8").split()[2] == "Z":
                return False
        except (OSError, IndexError):
            pass
    return True


def stop_owned_session() -> dict[str, Any]:
    info = read_session()
    if not info:
        return {"stopped": False, "reason": "no session record"}
    if not info.get("owned") or not info.get("pid"):
        session_path().unlink(missing_ok=True)
        return {"stopped": False, "reason": "session used an externally managed OpenOCD service"}
    pid = int(info["pid"])
    if not process_alive(pid):
        session_path().unlink(missing_ok=True)
        return {"stopped": True, "reason": "owned process was already stopped"}
    command_line = Path(f"/proc/{pid}/cmdline")
    if not command_line.exists():
        raise RuntimeError("Cannot verify the recorded OpenOCD process; refusing to signal it")
    executable = str(info.get("openocd_executable", "openocd"))
    command = command_line.read_bytes().replace(b"\0", b" ").decode(errors="replace")
    if executable not in command and Path(executable).name not in command:
        raise RuntimeError("Recorded PID no longer matches OpenOCD; refusing to signal it")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and process_alive(pid):
        time.sleep(0.05)
    if process_alive(pid):
        raise RuntimeError("OpenOCD did not stop within 3 seconds; no forced kill was sent")
    session_path().unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}
