"""Authenticated, local Unix-socket client for the persistent debug daemon."""

from __future__ import annotations

import json
import socket
from typing import Any

from .session_store import daemon_identity_matches, read_session


class SessionUnavailable(RuntimeError):
    pass


def request_session(operation: str, **params: Any) -> dict[str, Any]:
    info = read_session()
    if not info or not daemon_identity_matches(info):
        raise SessionUnavailable("No verified EmbeddedDebug session manager is running")
    socket_path = str(info.get("socket_path", ""))
    if not socket_path:
        raise SessionUnavailable("Debug session has no IPC socket record")
    request = {
        "session_id": info["session_id"],
        "token": info["token"],
        "operation": operation,
        "params": params,
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            timeout = (
                150.0
                if operation == "flash"
                else max(35.0, float(info.get("gdb_timeout", 10.0)) + 10.0)
            )
            channel.settimeout(timeout)
            channel.connect(socket_path)
            channel.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
            with channel.makefile("rb") as stream:
                response = stream.readline(1_048_577)
            if not response:
                raise SessionUnavailable("Debug session manager closed the IPC connection")
            if len(response) > 1_048_576:
                raise SessionUnavailable("Debug session manager response exceeded 1 MiB")
            parsed = json.loads(response)
    except (OSError, json.JSONDecodeError, TimeoutError) as exc:
        raise SessionUnavailable(f"Debug session IPC failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SessionUnavailable("Debug session returned an invalid IPC response")
    return parsed
