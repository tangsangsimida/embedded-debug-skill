"""Authenticated loopback IPC client for the persistent debug daemon."""

from __future__ import annotations

import json
from typing import Any

from .platforms import get_platform
from .session_store import daemon_identity_matches, read_session


class SessionUnavailable(RuntimeError):
    pass


def request_session(operation: str, **params: Any) -> dict[str, Any]:
    info = read_session()
    if not info or not daemon_identity_matches(info):
        raise SessionUnavailable("No verified EmbeddedDebug session manager is running")
    endpoint = info.get("ipc_endpoint")
    if not isinstance(endpoint, dict):
        # Keep sessions started by the prior POSIX-only CLI usable during upgrade.
        socket_path = info.get("socket_path")
        if isinstance(socket_path, str) and socket_path:
            endpoint = {"kind": "unix", "path": socket_path}
        else:
            raise SessionUnavailable("Debug session has no IPC endpoint record")
    request = {
        "session_id": info["session_id"],
        "token": info["token"],
        "operation": operation,
        "params": params,
    }
    try:
        with get_platform().connect_ipc(endpoint) as channel:
            timeout = (
                150.0
                if operation == "flash"
                else max(35.0, float(info.get("gdb_timeout", 10.0)) + 10.0)
            )
            channel.settimeout(timeout)
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
