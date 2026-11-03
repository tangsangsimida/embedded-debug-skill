"""Agent-friendly CLI with JSON envelopes and explicit destructive-operation authorization."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .build_info import build_info
from .config import ProjectConfig, discover_config, load_config
from .discovery import discover_project, openocd_launch_options
from .doctor import doctor
from .gdb.mi_transport import GDBError
from .models import Envelope
from .openocd.manager import OpenOCDError, OpenOCDManager
from .platforms import get_platform
from .session_manager import (
    SessionManagerError,
    list_sessions,
    session_operation,
    session_status,
    start_session,
    stop_session,
)
from .session_store import (
    current_session_name,
    daemon_identity_matches,
    read_session,
    set_session_name,
    validate_session_name,
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="edbg", description="OpenOCD-driven universal embedded debugger"
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--json", action="store_true", help="emit the standard JSON envelope")
    p.add_argument("--config", type=Path, help="project TOML configuration")
    p.add_argument("--gdb", help="GDB executable override")
    p.add_argument("--openocd", help="OpenOCD executable override")
    p.add_argument(
        "--session",
        type=validate_session_name,
        default="default",
        help="isolated persistent debug session name (default: default)",
    )
    p.add_argument(
        "--gdb-timeout",
        type=float,
        default=None,
        help="default timeout in seconds for GDB/MI commands",
    )
    sub = p.add_subparsers(dest="command", required=True)
    command_parsers: dict[str, argparse.ArgumentParser] = {}

    def command_parser(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        child = sub.add_parser(name, **kwargs)
        command_parsers[name] = child
        child.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="emit the standard JSON envelope",
        )
        child.add_argument("--session", type=validate_session_name, default=argparse.SUPPRESS)
        return child

    doctor_command = command_parser("doctor", help="inspect Python and system toolchain")
    doctor_command.add_argument(
        "--verbose", action="store_true", help="show complete tool executable names"
    )
    command_parser("config-validate", help="validate project TOML")
    command_parser("build-info", help="show CLI version and immutable source fingerprint")
    command_parser("probe-list", help="list OpenOCD-accessible debug probes (best effort)")
    command_parser("discover", help="discover existing build, OpenOCD, GDB, and ELF project setup")
    connect = command_parser("connect", help="connect to an OpenOCD GDB server")
    connect.add_argument("--interface")
    connect.add_argument("--target")
    connect.add_argument("--board")
    connect.add_argument("--openocd-config")
    connect.add_argument("--openocd-command", action="append", default=[])
    connect.add_argument("--elf", type=Path)
    connect.add_argument("--host")
    connect.add_argument("--gdb-port", type=int)
    connect.add_argument("--adapter-khz", type=int)
    connect.add_argument("--reuse-server", action="store_true")
    connect.add_argument("--keep-server", action="store_true")
    for name in (
        "status",
        "stop",
        "reset",
        "halt",
        "continue",
        "step",
        "next",
        "registers",
        "backtrace",
        "threads",
        "fault-analyze",
        "snapshot",
        "breakpoint-list",
    ):
        child = command_parser(name)
        if name == "reset":
            child.add_argument("--authorize", action="store_true")
    bp = command_parser("breakpoint-add")
    bp.add_argument("location")
    bp = command_parser("breakpoint-remove")
    bp.add_argument("number", type=int)
    var = command_parser("variable-read")
    var.add_argument("expression")
    mem = command_parser("memory-read")
    mem.add_argument("address", type=parse_int)
    mem.add_argument("--length", type=int, default=16)
    write = command_parser("memory-write")
    write.add_argument("address", type=parse_int)
    write.add_argument("--value", type=parse_int, required=True)
    write.add_argument("--length", type=int, default=1)
    write.add_argument("--authorize", action="store_true")
    flash = command_parser("flash")
    flash.add_argument("--authorize", action="store_true")
    flash.add_argument("--authorize-command", action="store_true")
    flash.add_argument("--elf", type=Path)
    for action_name in ("continue", "step", "next"):
        command_parsers[action_name].add_argument(
            "--authorize-run",
            action="store_true",
            help="confirm that executing target code is authorized",
        )
    build = command_parser("build")
    build.add_argument("--authorize-run", action="store_true")
    build.add_argument(
        "--authorize-command",
        action="store_true",
        help="allow execution of the current workspace's build command",
    )

    def add_nested(
        group_name: str, actions: dict[str, tuple[str, tuple[tuple[str, dict[str, Any]], ...]]]
    ) -> None:
        group = command_parser(group_name)
        nested = group.add_subparsers(dest="action", required=True)
        for action_name, (canonical, positional) in actions.items():
            leaf = nested.add_parser(action_name)
            leaf.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
            for arg_name, options in positional:
                leaf.add_argument(arg_name, **options)
            leaf.set_defaults(canonical_command=canonical)

    add_nested("config", {"validate": ("config-validate", ())})
    add_nested("probe", {"list": ("probe-list", ())})
    add_nested("variable", {"read": ("variable-read", (("expression", {}),))})
    add_nested(
        "memory",
        {
            "read": (
                "memory-read",
                (
                    ("address", {"type": parse_int}),
                    ("--length", {"type": int, "default": 16}),
                ),
            ),
            "write": (
                "memory-write",
                (
                    ("address", {"type": parse_int}),
                    ("--value", {"type": parse_int, "required": True}),
                    ("--length", {"type": int, "default": 1}),
                    ("--authorize", {"action": "store_true"}),
                ),
            ),
        },
    )
    add_nested(
        "breakpoint",
        {
            "add": ("breakpoint-add", (("location", {}),)),
            "list": ("breakpoint-list", ()),
            "remove": ("breakpoint-remove", (("number", {"type": int}),)),
        },
    )
    add_nested(
        "thread",
        {
            "list": ("threads", ()),
            "inspect": ("thread-inspect", (("thread_id", {}),)),
            "select": ("thread-select", (("thread_id", {}),)),
        },
    )
    add_nested("fault", {"analyze": ("fault-analyze", ())})
    session_group = command_parser("session")
    session_actions = session_group.add_subparsers(dest="action", required=True)
    session_start = session_actions.add_parser("start")
    session_start.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    session_start.add_argument("--session", type=validate_session_name, default=argparse.SUPPRESS)
    for flag, options in (
        ("--interface", {}),
        ("--target", {}),
        ("--board", {}),
        ("--openocd-config", {}),
        ("--openocd-command", {"action": "append", "default": []}),
        ("--elf", {"type": Path}),
        ("--host", {}),
        ("--gdb-port", {"type": int}),
        ("--adapter-khz", {"type": int}),
        ("--reuse-server", {"action": "store_true"}),
    ):
        session_start.add_argument(flag, **options)
    session_start.set_defaults(canonical_command="session-start")
    for action_name in ("list", "status", "stop", "recover", "cancel"):
        leaf = session_actions.add_parser(action_name)
        leaf.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        leaf.add_argument("--session", type=validate_session_name, default=argparse.SUPPRESS)
        if action_name == "recover":
            leaf.add_argument(
                "--authorize-openocd-restart",
                action="store_true",
                help="authorize restarting the configured OpenOCD service if it has exited",
            )
        if action_name == "cancel":
            leaf.add_argument(
                "--authorize-run",
                action="store_true",
                help=(
                    "authorize interrupting the active GDB operation and possibly "
                    "halting target execution"
                ),
            )
        leaf.set_defaults(canonical_command=f"session-{action_name}")
    return p


def parse_int(value: str) -> int:
    return int(value, 0)


def _config(args: argparse.Namespace) -> ProjectConfig:
    path = args.config or discover_config()
    return load_config(path) if path else ProjectConfig()


def _emit(envelope: Envelope, as_json: bool) -> None:
    if as_json:
        print(json.dumps(envelope.as_dict(), ensure_ascii=False, default=str))
        if envelope.error:
            print(
                f"{envelope.error['code']}: {envelope.error['message']}",
                file=sys.stderr,
            )
    else:
        label = "OK" if envelope.success else "ERROR"
        print(f"[{label}] {envelope.command}")
        if envelope.data is not None:
            print(json.dumps(envelope.data, ensure_ascii=False, indent=2, default=str))
        if envelope.error:
            print(f"{envelope.error['code']}: {envelope.error['message']}", file=sys.stderr)
        for diagnostic in envelope.diagnostics:
            print(diagnostic, file=sys.stderr if not envelope.success else sys.stdout)


def _run_external(
    argv: Sequence[str], cwd: Path, as_json: bool
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    if as_json:
        for stream in (result.stdout, result.stderr):
            if stream:
                print(stream, end="" if stream.endswith("\n") else "\n", file=sys.stderr)
    else:
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(
                result.stderr,
                end="" if result.stderr.endswith("\n") else "\n",
                file=sys.stderr,
            )
    return result


def _audit(action: str, target: str) -> None:
    path = Path(os.environ.get("EDBG_AUDIT_LOG", get_platform().state_dir() / "audit.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    from datetime import UTC, datetime

    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session": current_session_name(),
        "action": action,
        "target": target,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _connection_settings(
    args: argparse.Namespace, cfg: ProjectConfig, *, allow_connect_options: bool
) -> dict[str, Any]:
    discovery = discover_project()
    ide = openocd_launch_options(discovery)
    active_servers = discovery["active_gdb_servers"]
    stored_session = read_session() or {}
    session_active = bool(stored_session and daemon_identity_matches(stored_session))
    if session_active and stored_session:
        ide.setdefault("host", stored_session.get("host", "127.0.0.1"))
        ide.setdefault("gdb_port", int(stored_session.get("gdb_port", 3333)))
    if cfg.config_path and not session_active:
        ide = {}
    explicit_port = getattr(args, "gdb_port", None) if allow_connect_options else None
    if len(active_servers) == 1 and not ide:
        ide["host"], ide["gdb_port"] = active_servers[0]["host"], active_servers[0]["port"]
    elif len(active_servers) > 1 and not ide and explicit_port is None:
        raise ValueError("Multiple local GDB servers detected; choose one with --gdb-port")

    def cli_value(name: str) -> Any:
        return getattr(args, name, None) if allow_connect_options else None

    host = (
        cli_value("host")
        or (stored_session.get("host") if session_active else None)
        or (cfg.openocd.host if cfg.config_path else ide.get("host"))
        or "127.0.0.1"
    )
    port = (
        explicit_port
        or (stored_session.get("gdb_port") if session_active else None)
        or (cfg.openocd.gdb_port if cfg.config_path else ide.get("gdb_port"))
        or 3333
    )
    openocd_config = cli_value("openocd_config") or (
        stored_session.get("openocd_config")
        if session_active
        else cfg.openocd.config
        if cfg.config_path
        else ide.get("config")
    )
    interface = cli_value("interface") or (
        stored_session.get("interface")
        if session_active
        else cfg.openocd.interface
        if cfg.config_path
        else ide.get("interface")
    )
    target = cli_value("target") or (
        stored_session.get("target_config")
        if session_active
        else cfg.openocd.target
        if cfg.config_path
        else ide.get("target")
    )
    board = cli_value("board") or (
        stored_session.get("board")
        if session_active
        else cfg.openocd.board
        if cfg.config_path
        else ide.get("board")
    )
    config_files = (
        []
        if cli_value("openocd_config")
        else stored_session.get("config_files", [])
        if session_active
        else []
        if cfg.config_path
        else ide.get("config_files", [])
    )
    commands = [
        *(
            stored_session.get("commands", [])
            if session_active
            else cfg.openocd.commands
            if cfg.config_path
            else ide.get("commands", [])
        ),
        *(getattr(args, "openocd_command", []) if allow_connect_options else []),
    ]
    elf_arg = getattr(args, "elf", None)
    existing_elfs = list(
        dict.fromkeys(
            item["path"] for item in discovery["elf_candidates"] if Path(item["path"]).is_file()
        )
    )
    if elf_arg is not None:
        elf = elf_arg
    elif session_active and stored_session and stored_session.get("elf"):
        elf = Path(stored_session["elf"])
    elif cfg.elf:
        elf = Path(cfg.elf)
        if not elf.is_absolute() and cfg.config_path:
            elf = cfg.config_path.parent / elf
    else:
        existing_elfs = list(
            dict.fromkeys(
                item["path"] for item in discovery["elf_candidates"] if Path(item["path"]).is_file()
            )
        )
        elf = Path(existing_elfs[0]) if len(existing_elfs) == 1 else None
    return {
        "discovery": discovery,
        "ide": ide,
        "existing_elfs": existing_elfs,
        "host": host,
        "gdb_port": port,
        "openocd_config": openocd_config,
        "interface": interface,
        "target": target,
        "board": board,
        "config_files": config_files,
        "server_args": []
        if cli_value("openocd_config")
        else stored_session.get("server_args", [])
        if session_active
        else []
        if cfg.config_path
        else ide.get("server_args", []),
        "commands": commands,
        "adapter_khz": (
            cli_value("adapter_khz")
            or (stored_session.get("adapter_khz") if session_active else None)
            or (cfg.openocd.adapter_khz if cfg.config_path else None)
        ),
        "elf": elf,
        "openocd_executable": (
            cli_value("openocd")
            or (stored_session.get("openocd_executable") if session_active else None)
            or (
                cfg.tools.openocd
                if cfg.config_path and not session_active
                else ide.get("server_path")
            )
            or cfg.tools.openocd
        ),
        "gdb_executable": (
            cli_value("gdb")
            or (stored_session.get("gdb_executable") if session_active else None)
            or (
                cfg.tools.gdb
                if cfg.config_path and not session_active
                else ide.get("gdb_executable")
            )
            or cfg.tools.gdb
        ),
        "gdb_timeout": getattr(args, "gdb_timeout", None)
        or (stored_session.get("gdb_timeout") if session_active else None)
        or 10.0,
    }


def _daemon_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "host": settings["host"],
        "gdb_port": settings["gdb_port"],
        "openocd_config": settings["openocd_config"],
        "interface": settings["interface"],
        "target_config": settings["target"],
        "board": settings["board"],
        "config_files": settings["config_files"],
        "server_args": settings["server_args"],
        "commands": settings["commands"],
        "adapter_khz": settings["adapter_khz"],
        "openocd_executable": settings["openocd_executable"],
        "gdb_executable": settings["gdb_executable"],
        "gdb_timeout": settings["gdb_timeout"],
        "elf": str(settings["elf"]) if settings["elf"] else None,
    }


def run(argv: Sequence[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    set_session_name(args.session)
    name = getattr(args, "canonical_command", args.command)
    try:
        cfg = _config(args)
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
            if not args.authorize_run:
                raise PermissionError(
                    "Cancelling a GDB operation requires explicit --authorize-run"
                )
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
            if not (args.authorize_command or args.authorize_run):
                raise PermissionError(
                    "Build executes a workspace command; pass --authorize-command "
                    "after reviewing it"
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
            if name in {"continue", "step", "next"} and not args.authorize_run:
                raise PermissionError(f"{name} requires explicit --authorize-run")
            if name in {"reset", "memory-write", "flash"} and not args.authorize:
                raise PermissionError(f"{name} requires explicit --authorize")
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
