"""Debug-session lifecycle and connection CLI handlers."""

from __future__ import annotations

import argparse

from ..config import ProjectConfig
from ..models import Envelope
from ..openocd.manager import OpenOCDManager
from ..safety import require_authorized
from ..session_manager import (
    list_sessions,
    session_operation,
    session_status,
    start_session,
    stop_session,
)
from .audit import audit
from .connection import (
    _connection_settings as connection_settings,
)
from .connection import (
    _daemon_settings as daemon_settings,
)
from .output import emit


def handle_session_command(
    name: str, args: argparse.Namespace, cfg: ProjectConfig
) -> int | None:
    if name == "session-status":
        data = session_status()
        emit(
            Envelope(
                name,
                "error" not in data,
                target=data.get("target", {}),
                target_state=data.get("target_state", "unknown"),
                data=data,
                error=(
                    {"code": "SESSION_UNAVAILABLE", "message": data["error"]}
                    if "error" in data
                    else None
                ),
            ),
            args.json,
        )
        return 0 if "error" not in data else 2
    if name == "session-list":
        emit(Envelope(name, True, data={"sessions": list_sessions()}), args.json)
        return 0
    if name == "session-stop":
        data = stop_session()
        emit(Envelope(name, True, data=data, target_state="disconnected"), args.json)
        return 0
    if name == "session-recover":
        response = session_operation(
            "recover",
            authorize_openocd_restart=bool(args.authorize_openocd_restart),
        )
        if args.authorize_openocd_restart:
            audit("openocd-restart", "session recovery")
        emit(
            Envelope(
                name,
                True,
                target=response.get("target", {}),
                target_state=response.get("target_state", "unknown"),
                data=response.get("data"),
            ),
            args.json,
        )
        return 0
    if name == "session-cancel":
        require_authorized(
            "Cancelling a GDB operation", args.authorize_run, "--authorize-run"
        )
        audit("cancel-gdb-operation", "active debug session")
        response = session_operation("cancel", authorized_run=True)
        emit(
            Envelope(
                name,
                True,
                target=response.get("target", {}),
                target_state=response.get("target_state", "unknown"),
                data=response.get("data"),
            ),
            args.json,
        )
        return 0
    if name in {"connect", "session-start"}:
        if args.gdb_timeout is not None and args.gdb_timeout <= 0:
            raise ValueError("--gdb-timeout must be a positive number")
        settings = connection_settings(args, cfg, allow_connect_options=True)
        if settings["host"] not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Only loopback OpenOCD connections are allowed by default")
        if not OpenOCDManager.port_open(settings["host"], settings["gdb_port"]) and not any(
            (
                settings["openocd_config"],
                settings["interface"],
                settings["target"],
                settings["board"],
                settings["config_files"],
                settings["server_args"],
                settings["commands"],
            )
        ):
            raise ValueError(
                "No running local GDB server or existing project OpenOCD "
                "configuration was found. Run edbg discover --json and pass "
                "the project's existing settings."
            )
        audit("connect", f"{settings['host']}:{settings['gdb_port']}")
        data = start_session(daemon_settings(settings))
        data.setdefault("architecture", data.get("target", {}).get("architecture"))
        emit(
            Envelope(
                name,
                True,
                target=data.get("target", {}),
                target_state=data.get("target_state", "unknown"),
                data={
                    **data,
                    "session": "started" if name == "session-start" else "connected",
                },
                diagnostics=(
                    ["Multiple ELF files were found; pass --elf to select symbols."]
                    if not settings["elf"] and len(settings["existing_elfs"]) > 1
                    else []
                ),
            ),
            args.json,
        )
        return 0
    return None
