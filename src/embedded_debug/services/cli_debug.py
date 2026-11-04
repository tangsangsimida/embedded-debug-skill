"""Build and target-operation CLI handlers."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..config import ProjectConfig
from ..discovery import discover_project
from ..gdb.controller import is_simple_variable_path
from ..gdb.mi_transport import GDBError
from ..models import Envelope
from ..openocd.manager import OpenOCDError, OpenOCDManager
from ..safety import require_authorized
from ..session_manager import (
    SessionManagerError,
    session_operation,
    session_status,
    start_session,
    stop_session,
)
from ..session_store import read_session
from .audit import audit, authorize_operation
from .connection import (
    _connection_settings as connection_settings,
)
from .connection import (
    _daemon_settings as daemon_settings,
)
from .output import emit, run_external


def handle_debug_command(
    name: str, args: argparse.Namespace, cfg: ProjectConfig
) -> int | None:
    if name == "build":
        require_authorized(
            "Build executes a workspace command",
            args.authorize_command or args.authorize_run,
            "--authorize-command after reviewing it",
        )
        root = cfg.config_path.parent if cfg.config_path else Path.cwd()
        if cfg.build_command:
            build_command = cfg.build_command
            build_cwd = root / cfg.build_dir
        else:
            candidate = discover_project(root)["build"]
            if not candidate or not candidate.get("argv"):
                raise ValueError(
                    "No runnable build command was found; configure the existing "
                    "project build command."
                )
            build_command = candidate["argv"]
            build_cwd = root
        audit("build-command", " ".join(build_command))
        build_result = run_external(build_command, build_cwd, args.json)
        ok = build_result.returncode == 0
        emit(
            Envelope(
                name,
                ok,
                data={"exit_code": build_result.returncode},
                error=None
                if ok
                else {"code": "BUILD_FAILED", "message": "Build command failed"},
            ),
            args.json,
        )
        return 0 if ok else 1

    persistent_operations = {
        "status",
        "halt",
        "continue",
        "step",
        "next",
        "reset",
        "registers",
        "backtrace",
        "threads",
        "thread-inspect",
        "thread-select",
        "fault-analyze",
        "snapshot",
        "breakpoint-list",
        "breakpoint-add",
        "breakpoint-remove",
        "variable-read",
        "variable-evaluate",
        "memory-read",
        "memory-write",
        "flash",
    }
    if name in persistent_operations:
        if args.gdb_timeout is not None and args.gdb_timeout <= 0:
            raise ValueError("--gdb-timeout must be a positive number")
        settings = connection_settings(args, cfg, allow_connect_options=False)
        if settings["host"] not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Only loopback OpenOCD connections are allowed by default")
        audit_target = str(settings["elf"] or f"{settings['host']}:{settings['gdb_port']}")
        if name == "memory-write":
            audit_target = f"0x{args.address:x}"
        elif name == "breakpoint-add":
            audit_target = args.location
        elif name == "breakpoint-remove":
            audit_target = str(args.number)
        expression = str(args.expression) if name == "variable-read" else ""
        arbitrary_expression = name == "variable-read" and not is_simple_variable_path(
            expression
        )
        operation = "variable-evaluate" if arbitrary_expression else name
        if arbitrary_expression:
            audit_target = expression
        authorize_operation(
            operation,
            audit_target,
            authorized_run=bool(getattr(args, "authorize_run", False)),
            authorized=bool(getattr(args, "authorize", False)),
            authorized_expression=bool(getattr(args, "authorize_expression", False)),
        )
        if name == "flash":
            project_root = cfg.config_path.parent if cfg.config_path else Path.cwd()
            project = discover_project(project_root)
            flash_task = project.get("flash")
            if flash_task and flash_task.get("argv"):
                if not args.authorize_command:
                    raise PermissionError(
                        "Flash task executes a workspace command; review it and pass "
                        "--authorize-command"
                    )
                had_session = bool(session_status().get("active"))
                if had_session:
                    stop_session()
                task_argv = flash_task["argv"]
                task_cwd = Path(project["project_root"])
                audit("flash-command", " ".join(task_argv))
                flash_result = run_external(task_argv, task_cwd, args.json)
                reconnect = None
                if had_session and flash_result.returncode == 0:
                    try:
                        reconnect = start_session(daemon_settings(settings))
                    except (SessionManagerError, OpenOCDError, GDBError) as exc:
                        reconnect = {"error": str(exc)}
                succeeded = flash_result.returncode == 0
                emit(
                    Envelope(
                        name,
                        succeeded,
                        target=(reconnect or {}).get("target", {}),
                        target_state=(reconnect or {}).get("target_state", "unknown"),
                        data={
                            "source": flash_task["source"],
                            "exit_code": flash_result.returncode,
                            "session_reconnected": bool(reconnect and "error" not in reconnect)
                            if had_session
                            else None,
                        },
                        diagnostics=(
                            [reconnect["error"]] if reconnect and "error" in reconnect else []
                        ),
                        error=None
                        if succeeded
                        else {"code": "FLASH_FAILED", "message": "Workspace flash task failed"},
                    ),
                    args.json,
                )
                return 0 if succeeded else 1
        if (
            not read_session()
            and not OpenOCDManager.port_open(settings["host"], settings["gdb_port"])
            and not any(
                (
                    settings["openocd_config"],
                    settings["interface"],
                    settings["target"],
                    settings["board"],
                    settings["config_files"],
                    settings["server_args"],
                    settings["commands"],
                )
            )
        ):
            raise ValueError(
                "No live debug session or existing project OpenOCD configuration was found; "
                "run edbg discover --json and supply existing settings."
            )
        start_session(daemon_settings(settings))
        params: dict[str, Any] = {}
        if name == "variable-read":
            params["expression"] = args.expression
        elif name in {"memory-read", "memory-write"}:
            params.update({"address": args.address, "length": args.length})
            if name == "memory-write":
                params["value"] = args.value
                params["authorized"] = True
        elif name == "breakpoint-add":
            params["location"] = args.location
        elif name == "breakpoint-remove":
            params["number"] = args.number
        elif name == "thread-inspect":
            params["thread_id"] = args.thread_id
        elif name == "thread-select":
            params["thread_id"] = args.thread_id
        if name in {"halt", "continue", "step", "next"}:
            params["authorized_run"] = True
        if name in {
            "reset",
            "memory-write",
            "flash",
            "breakpoint-add",
            "breakpoint-remove",
        }:
            params["authorized"] = True
        if name == "memory-read" and not 1 <= args.length <= 4096:
            raise ValueError("memory read length must be between 1 and 4096 bytes")
        if name == "memory-write":
            if (
                not 1 <= args.length <= 8
                or args.value < 0
                or args.value >= 1 << (8 * args.length)
            ):
                raise ValueError("memory write value must fit a 1..8 byte length")
        if arbitrary_expression:
            params["authorized_expression"] = True
        response = session_operation(operation, **params)
        response_data = response.get("data")
        target_info = response.get("target", {})
        target_state = response.get("target_state", "unknown")
        if isinstance(response_data, dict):
            target_info = response_data.get("target", target_info)
            target_state = response_data.get("target_state", target_state)
        emit(
            Envelope(
                name,
                True,
                target=target_info,
                target_state=target_state,
                data=response_data,
                diagnostics=(
                    [f"Capabilities: {response_data['capabilities']}"]
                    if name == "status"
                    and isinstance(response_data, dict)
                    and "capabilities" in response_data
                    else []
                ),
            ),
            args.json,
        )
        return 0
    return None
