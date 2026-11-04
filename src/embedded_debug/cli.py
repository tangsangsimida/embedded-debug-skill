"""Agent-friendly CLI with JSON envelopes and explicit destructive-operation authorization."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .build_info import build_info
from .cli_parser import parser
from .config import ProjectConfig, discover_config, load_config
from .discovery import discover_project
from .doctor import doctor
from .environment import inspect_environment, install_user_cli, remediation_plan
from .gdb.mi_transport import GDBError
from .models import Envelope
from .openocd.manager import OpenOCDError, OpenOCDManager
from .safety import require_authorized
from .services.audit import audit as _audit
from .services.connection import _connection_settings, _daemon_settings
from .services.output import emit as _emit
from .services.output import run_external as _run_external
from .session_manager import (
    SessionManagerError,
    list_sessions,
    session_operation,
    session_status,
    start_session,
    stop_session,
)
from .session_store import read_session, set_session_name


def _config(args: argparse.Namespace) -> ProjectConfig:
    path = args.config or discover_config()
    return load_config(path) if path else ProjectConfig()


def run(argv: Sequence[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    set_session_name(args.session)
    name = getattr(args, "canonical_command", args.command)
    try:
        cfg = _config(args)
        if name == "environment-inspect":
            inspected = inspect_environment(
                {
                    **{k: str(v) for k, v in asdict(cfg.tools).items()},
                    **({"gdb": args.gdb} if args.gdb else {}),
                    **({"openocd": args.openocd} if args.openocd else {}),
                }
            )
            _emit(Envelope(name, True, data=inspected), args.json)
            return 0
        if name == "environment-plan":
            inspected = inspect_environment(
                {k: str(v) for k, v in asdict(cfg.tools).items()}
            )
            _emit(Envelope(name, True, data={"actions": remediation_plan(inspected)}), args.json)
            return 0
        if name == "environment-install-cli":
            require_authorized("Installing the Python CLI", args.authorize)
            _audit("install-python-cli", f"version={__version__}")
            result = install_user_cli(args.source, authorized=True)
            _emit(Envelope(name, True, data=result), args.json)
            return 0
        if name == "doctor":
            result = doctor(
                {
                    **{k: str(v) for k, v in asdict(cfg.tools).items()},
                    **({"gdb": args.gdb} if args.gdb else {}),
                    **({"openocd": args.openocd} if args.openocd else {}),
                },
                elf=(
                    str((cfg.config_path.parent if cfg.config_path else Path.cwd()) / cfg.elf)
                    if cfg.elf
                    else None
                ),
                verbose=args.verbose,
            )
            env = Envelope(
                name,
                True,
                data=result,
                diagnostics=[]
                if result["cli_ready"]
                else ["Required Python runtime is available; optional debug tools may be missing."],
            )
            _emit(env, args.json)
            return 0
        if name == "build-info":
            _emit(Envelope(name, True, data=build_info()), args.json)
            return 0
        if name == "discover":
            _emit(Envelope(name, True, data=discover_project()), args.json)
            return 0
        if name == "config-validate":
            _emit(
                Envelope(
                    name,
                    True,
                    data={
                        "valid": True,
                        "config": str(cfg.config_path) if cfg.config_path else None,
                    },
                ),
                args.json,
            )
            return 0
        if name == "probe-list":
            _emit(
                Envelope(
                    name,
                    True,
                    data={
                        "status": "unknown",
                        "message": (
                            "OpenOCD probe enumeration depends on adapter and OpenOCD build; "
                            "no universal list command exists."
                        ),
                    },
                ),
                args.json,
            )
            return 0
        if name == "session-status":
            data = session_status()
            _emit(
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
            _emit(
                Envelope(name, True, data={"sessions": list_sessions()}),
                args.json,
            )
            return 0
        if name == "session-stop":
            data = stop_session()
            _emit(Envelope(name, True, data=data, target_state="disconnected"), args.json)
            return 0
        if name == "session-recover":
            response = session_operation(
                "recover",
                authorize_openocd_restart=bool(args.authorize_openocd_restart),
            )
            if args.authorize_openocd_restart:
                _audit("openocd-restart", "session recovery")
            _emit(
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
            require_authorized("Cancelling a GDB operation", args.authorize_run, "--authorize-run")
            _audit("cancel-gdb-operation", "active debug session")
            response = session_operation("cancel", authorized_run=True)
            _emit(
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
            settings = _connection_settings(args, cfg, allow_connect_options=True)
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
                    "configuration was found. "
                    "Run edbg discover --json and pass the project's existing settings."
                )
            _audit("connect", f"{settings['host']}:{settings['gdb_port']}")
            data = start_session(_daemon_settings(settings))
            data.setdefault("architecture", data.get("target", {}).get("architecture"))
            _emit(
                Envelope(
                    name,
                    True,
                    target=data.get("target", {}),
                    target_state=data.get("target_state", "unknown"),
                    data={**data, "session": "started" if name == "session-start" else "connected"},
                    diagnostics=(
                        ["Multiple ELF files were found; pass --elf to select symbols."]
                        if not settings["elf"] and len(settings["existing_elfs"]) > 1
                        else []
                    ),
                ),
                args.json,
            )
            return 0

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
            _audit("build-command", " ".join(build_command))
            build_result = _run_external(build_command, build_cwd, args.json)
            ok = build_result.returncode == 0
            _emit(
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
            "memory-read",
            "memory-write",
            "flash",
        }
        if name in persistent_operations:
            if args.gdb_timeout is not None and args.gdb_timeout <= 0:
                raise ValueError("--gdb-timeout must be a positive number")
            if name in {"continue", "step", "next"}:
                require_authorized(name, args.authorize_run, "--authorize-run")
            if name in {"reset", "memory-write", "flash"}:
                require_authorized(name, args.authorize)
            settings = _connection_settings(args, cfg, allow_connect_options=False)
            if settings["host"] not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("Only loopback OpenOCD connections are allowed by default")
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
                    _audit("flash-command", " ".join(task_argv))
                    flash_result = _run_external(task_argv, task_cwd, args.json)
                    reconnect = None
                    if had_session and flash_result.returncode == 0:
                        try:
                            reconnect = start_session(_daemon_settings(settings))
                        except (SessionManagerError, OpenOCDError, GDBError) as exc:
                            reconnect = {"error": str(exc)}
                    succeeded = flash_result.returncode == 0
                    _emit(
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
            start_session(_daemon_settings(settings))
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
            elif name == "flash":
                params["authorized"] = True
            elif name == "reset":
                params["authorized"] = True
            if name in {"continue", "step", "next"}:
                params["authorized_run"] = True
            if name == "memory-read" and not 1 <= args.length <= 4096:
                raise ValueError("memory read length must be between 1 and 4096 bytes")
            if name == "memory-write":
                if (
                    not 1 <= args.length <= 8
                    or args.value < 0
                    or args.value >= 1 << (8 * args.length)
                ):
                    raise ValueError("memory write value must fit a 1..8 byte length")
            if name in {"continue", "step", "next", "halt", "reset", "flash"}:
                _audit(name, str(settings["elf"] or f"{settings['host']}:{settings['gdb_port']}"))
            elif name == "memory-write":
                _audit(name, f"0x{args.address:x}")
            elif name == "breakpoint-add":
                _audit(name, args.location)
            elif name == "breakpoint-remove":
                _audit(name, str(args.number))
            response = session_operation(name, **params)
            response_data = response.get("data")
            target_info = response.get("target", {})
            target_state = response.get("target_state", "unknown")
            if isinstance(response_data, dict):
                target_info = response_data.get("target", target_info)
                target_state = response_data.get("target_state", target_state)
            _emit(
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
        raise RuntimeError(f"Unsupported command: {name}")
    except (ValueError, OSError, GDBError, OpenOCDError, PermissionError, RuntimeError) as exc:
        code = "AUTHORIZATION_REQUIRED" if isinstance(exc, PermissionError) else "OPERATION_FAILED"
        _emit(Envelope(name, False, error={"code": code, "message": str(exc)}), args.json)
        return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
