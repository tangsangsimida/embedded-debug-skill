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
from .config import ProjectConfig, discover_config, load_config
from .discovery import discover_project, openocd_launch_options
from .doctor import doctor
from .gdb.controller import GDBController
from .gdb.mi_transport import GDBError, MITransport
from .models import Envelope
from .openocd.manager import OpenOCDError, OpenOCDManager
from .session_store import (
    process_alive,
    read_session,
    save_session,
    stop_owned_session,
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
        return child

    doctor_command = command_parser("doctor", help="inspect Python and system toolchain")
    doctor_command.add_argument(
        "--verbose", action="store_true", help="show complete tool executable names"
    )
    command_parser("config-validate", help="validate project TOML")
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
    flash.add_argument("--elf", type=Path)
    for action_name in ("continue", "step", "next"):
        command_parsers[action_name].add_argument(
            "--authorize-run",
            action="store_true",
            help="confirm that executing target code is authorized",
        )
    build = command_parser("build")
    build.add_argument("--authorize-run", action="store_true")

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
        },
    )
    add_nested("fault", {"analyze": ("fault-analyze", ())})
    session_group = command_parser("session")
    session_actions = session_group.add_subparsers(dest="action", required=True)
    session_start = session_actions.add_parser("start")
    session_start.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
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
    for action_name in ("status", "stop"):
        leaf = session_actions.add_parser(action_name)
        leaf.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
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
    path = Path(os.environ.get("EDBG_AUDIT_LOG", Path.home() / ".local/state/edbg/audit.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    from datetime import UTC, datetime

    entry = {"timestamp": datetime.now(UTC).isoformat(), "action": action, "target": target}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _connection_settings(
    args: argparse.Namespace, cfg: ProjectConfig, *, allow_connect_options: bool
) -> dict[str, Any]:
    discovery = discover_project()
    ide = openocd_launch_options(discovery)
    active_servers = discovery["active_gdb_servers"]
    stored_session = read_session()
    session_active = bool(
        stored_session
        and OpenOCDManager.port_open(
            str(stored_session.get("host", "127.0.0.1")), int(stored_session.get("gdb_port", 3333))
        )
    )
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
        or (cfg.openocd.host if cfg.config_path else ide.get("host"))
        or "127.0.0.1"
    )
    port = (
        explicit_port or (cfg.openocd.gdb_port if cfg.config_path else ide.get("gdb_port")) or 3333
    )
    openocd_config = cli_value("openocd_config") or (
        cfg.openocd.config if cfg.config_path and not session_active else ide.get("config")
    )
    interface = cli_value("interface") or (
        cfg.openocd.interface if cfg.config_path and not session_active else ide.get("interface")
    )
    target = cli_value("target") or (cfg.openocd.target if cfg.config_path else ide.get("target"))
    board = cli_value("board") or (cfg.openocd.board if cfg.config_path else ide.get("board"))
    config_files = (
        []
        if (cfg.config_path and not session_active) or cli_value("openocd_config")
        else ide.get("config_files", [])
    )
    commands = [
        *(
            cfg.openocd.commands
            if cfg.config_path and not session_active
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
        if cfg.config_path or cli_value("openocd_config")
        else ide.get("server_args", []),
        "commands": commands,
        "adapter_khz": (
            cli_value("adapter_khz") or (cfg.openocd.adapter_khz if cfg.config_path else None)
        ),
        "elf": elf,
        "openocd_executable": (
            cli_value("openocd")
            or (
                cfg.tools.openocd
                if cfg.config_path and not session_active
                else ide.get("server_path")
            )
            or cfg.tools.openocd
        ),
        "gdb_executable": (
            cli_value("gdb")
            or (stored_session.get("gdb_executable") if stored_session else None)
            or (
                cfg.tools.gdb
                if cfg.config_path and not session_active
                else ide.get("gdb_executable")
            )
            or cfg.tools.gdb
        ),
    }


def run(argv: Sequence[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
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
        if name == "session-status":
            info = read_session()
            if not info:
                _emit(Envelope(name, True, data={"active": False}), args.json)
            else:
                alive = bool(
                    info.get("pid") and process_alive(int(info["pid"]))
                    if info.get("owned")
                    else OpenOCDManager.port_open(
                        str(info.get("host", "127.0.0.1")), int(info.get("gdb_port", 3333))
                    )
                )
                info["active"] = alive and OpenOCDManager.port_open(
                    str(info.get("host", "127.0.0.1")), int(info.get("gdb_port", 3333))
                )
                info["target_state"] = "unknown"
                _emit(Envelope(name, True, target_state="unknown", data=info), args.json)
            return 0
        if name == "session-stop":
            result = stop_owned_session()
            _emit(Envelope(name, True, data=result), args.json)
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
        if name in {"connect", "session-start"}:
            existing_session = read_session()
            if (
                name == "session-start"
                and existing_session
                and OpenOCDManager.port_open(
                    str(existing_session.get("host", "127.0.0.1")),
                    int(existing_session.get("gdb_port", 3333)),
                )
            ):
                raise ValueError(
                    "An EmbeddedDebug session is already active; run session status or stop first"
                )
            settings = _connection_settings(args, cfg, allow_connect_options=True)
            host = settings["host"]
            gdb_port = settings["gdb_port"]
            openocd_config = settings["openocd_config"]
            interface, target_config, board = (
                settings["interface"],
                settings["target"],
                settings["board"],
            )
            commands = settings["commands"]
            adapter_khz = settings["adapter_khz"]
            config_files = settings["config_files"]
            server_args = settings["server_args"]
            elf = settings["elf"]
            if not OpenOCDManager.port_open(host, gdb_port) and not any(
                [
                    openocd_config,
                    interface,
                    target_config,
                    board,
                    config_files,
                    settings["server_args"],
                ]
            ):
                raise ValueError(
                    "No running local GDB server or unambiguous OpenOCD project config was found. "
                    "Run edbg discover --json and supply existing project settings; "
                    "no target.cfg is generated."
                )
        elif name in {
            "status",
            "halt",
            "continue",
            "step",
            "next",
            "registers",
            "backtrace",
            "threads",
            "thread-inspect",
            "fault-analyze",
            "snapshot",
            "breakpoint-list",
            "breakpoint-add",
            "breakpoint-remove",
            "variable-read",
            "memory-read",
            "memory-write",
            "reset",
        }:
            settings = _connection_settings(args, cfg, allow_connect_options=False)
            host, gdb_port = settings["host"], settings["gdb_port"]
            openocd_config = settings["openocd_config"]
            interface, target_config, board = (
                settings["interface"],
                settings["target"],
                settings["board"],
            )
            commands, adapter_khz = settings["commands"], settings["adapter_khz"]
            config_files, elf = settings["config_files"], settings["elf"]
            server_args = settings["server_args"]
        elif name == "flash":
            settings = _connection_settings(args, cfg, allow_connect_options=False)
            host, gdb_port = settings["host"], settings["gdb_port"]
            openocd_config, interface = settings["openocd_config"], settings["interface"]
            target_config, board = settings["target"], settings["board"]
            commands, adapter_khz = settings["commands"], settings["adapter_khz"]
            config_files, elf = settings["config_files"], settings["elf"]
            server_args = settings["server_args"]
        else:
            host = None
        if name == "flash":
            if not args.authorize:
                raise PermissionError("flash requires explicit --authorize")
            project = discover_project()
            flash_task = project.get("flash")
            if flash_task and flash_task.get("argv"):
                _audit("flash", str(flash_task.get("label") or flash_task["source"]))
                flash_result = _run_external(
                    flash_task["argv"], Path(project["project_root"]), args.json
                )
                succeeded = flash_result.returncode == 0
                _emit(
                    Envelope(
                        name,
                        succeeded,
                        data={"source": flash_task["source"], "exit_code": flash_result.returncode},
                        error=None
                        if succeeded
                        else {
                            "code": "FLASH_FAILED",
                            "message": "Existing project flash task failed",
                        },
                    ),
                    args.json,
                )
                return 0 if succeeded else 1
        if name in {
            "connect",
            "session-start",
            "status",
            "halt",
            "continue",
            "step",
            "next",
            "registers",
            "backtrace",
            "threads",
            "thread-inspect",
            "fault-analyze",
            "snapshot",
            "breakpoint-list",
            "breakpoint-add",
            "breakpoint-remove",
            "variable-read",
            "memory-read",
            "memory-write",
            "flash",
            "reset",
        }:
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("Only loopback OpenOCD connections are allowed by default")
            if name in {"flash", "reset", "memory-write"} and not args.authorize:
                raise PermissionError(f"{name} requires explicit --authorize")
            if name in {"continue", "step", "next"} and not args.authorize_run:
                raise PermissionError(f"{name} requires explicit --authorize-run")
            if name == "memory-read" and not 1 <= args.length <= 4096:
                raise ValueError("memory read length must be between 1 and 4096 bytes")
            if name == "memory-write":
                if (
                    not 1 <= args.length <= 8
                    or args.value < 0
                    or args.value >= 1 << (8 * args.length)
                ):
                    raise ValueError(
                        "memory write value does not fit the requested 1..8 byte length"
                    )
                _audit(name, f"0x{args.address:x}")
            if name in {"flash", "reset"}:
                _audit(name, str(elf or "configured target"))
            if name in {"continue", "step", "next", "halt"}:
                _audit(name, name)
            if name in {"connect", "session-start"}:
                _audit(name, f"{host}:{gdb_port}")
            if name == "breakpoint-add":
                _audit(name, args.location)
            if name == "breakpoint-remove":
                _audit(name, str(args.number))
            owned_manager = not OpenOCDManager.port_open(host, gdb_port)
            openocd_executable = (
                settings["openocd_executable"] if name != "flash" else cfg.tools.openocd
            )
            manager = (
                OpenOCDManager(
                    openocd_executable,
                    host,
                    gdb_port,
                    Path(".edbg/openocd.log"),
                )
                if owned_manager
                else None
            )
            gdb_executable = (
                settings["gdb_executable"] if name != "flash" else (args.gdb or cfg.tools.gdb)
            )
            transport = MITransport(gdb_executable)
            try:
                if manager:
                    manager.start(
                        openocd_config,
                        interface,
                        target_config,
                        board,
                        commands,
                        adapter_khz,
                        config_files=config_files,
                        extra_args=server_args,
                    )
                controller = GDBController(transport)
                data: Any = controller.connect(host, gdb_port, str(elf) if elf else None)
                if name == "connect":
                    pass
                elif name == "status":
                    data = {
                        "connection": "connected",
                        "architecture": controller.architecture,
                        "capabilities": data["capabilities"],
                    }
                elif name == "registers":
                    data = controller.registers_read()
                elif name == "variable-read":
                    data = {
                        "expression": args.expression,
                        "value": controller.variable_read(args.expression),
                    }
                elif name == "backtrace":
                    data = controller.backtrace()
                elif name == "breakpoint-add":
                    data = controller.breakpoint_add(args.location)
                elif name == "breakpoint-list":
                    data = controller.breakpoint_list()
                elif name == "breakpoint-remove":
                    data = transport.command(f"-break-delete {args.number}").results
                elif name == "memory-read":
                    data = transport.command(
                        f"-data-read-memory-bytes 0x{args.address:x} {args.length}"
                    ).results
                elif name == "memory-write":
                    payload = args.value.to_bytes(args.length, "little").hex()
                    data = transport.command(
                        f'-data-write-memory-bytes 0x{args.address:x} "{payload}"'
                    ).results
                elif name == "halt":
                    controller.halt()
                    data = {"state": transport.state}
                elif name == "continue":
                    controller.resume()
                    data = {"state": transport.state}
                elif name == "step":
                    transport.command("-exec-step")
                    data = {"state": transport.state}
                elif name == "next":
                    transport.command("-exec-next")
                    data = {"state": transport.state}
                elif name == "threads":
                    data = {
                        "gdb_threads": transport.command("-thread-info").results,
                        "rtos_awareness": controller.capabilities.rtos_awareness,
                    }
                elif name == "thread-inspect":
                    thread_info = transport.command("-thread-info").results or {}
                    threads = thread_info.get("threads", [])
                    requested = str(args.thread_id)
                    item = next(
                        (thread for thread in threads if str(thread.get("id")) == requested),
                        None,
                    )
                    if item is None:
                        raise ValueError(f"GDB thread {requested} was not reported")
                    data = {
                        "thread": item,
                        "rtos_awareness": controller.capabilities.rtos_awareness,
                    }
                elif name == "snapshot":
                    data = {
                        "registers": controller.registers_read(),
                        "backtrace": controller.backtrace(),
                        "architecture": controller.architecture,
                    }
                elif name == "fault-analyze":
                    raise RuntimeError(
                        "No architecture-specific fault diagnostic plugin is available"
                    )
                elif name == "reset":
                    data = transport.command(
                        '-interpreter-exec console "monitor reset halt"'
                    ).results
                elif name == "flash":
                    if not elf or not elf.is_file():
                        raise ValueError(
                            "flash requires an existing ELF via --elf or [project].elf"
                        )
                    data = transport.command(
                        '-interpreter-exec console "load"', timeout=120
                    ).results
                target_info = {"architecture": controller.architecture, "connection": "connected"}
                diagnostics = []
                if name == "connect" and not elf and len(settings["existing_elfs"]) > 1:
                    diagnostics.append(
                        "Multiple ELF files were found; no symbol file was selected. "
                        "Pass --elf to enable source/variable lookup."
                    )
                keep_openocd = name == "session-start" or bool(getattr(args, "keep_server", False))
                if keep_openocd:
                    save_session(
                        {
                            "pid": manager.process.pid if manager and manager.process else None,
                            "owned": bool(manager and manager.process),
                            "host": host,
                            "gdb_port": gdb_port,
                            "gdb_executable": gdb_executable,
                            "elf": str(elf) if elf else None,
                            "openocd_executable": openocd_executable,
                            "started_from": str(cfg.config_path)
                            if cfg.config_path
                            else "project discovery",
                        }
                    )
                    data["session"] = "started" if name == "session-start" else "kept"
                _emit(
                    Envelope(
                        name,
                        True,
                        target=target_info,
                        target_state=transport.state,
                        data=data,
                        diagnostics=diagnostics,
                    ),
                    args.json,
                )
                return 0
            finally:
                transport.close()
                if manager and not (name == "session-start" or getattr(args, "keep_server", False)):
                    manager.stop()
        if name == "build":
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
        if name in {"threads", "fault-analyze", "snapshot"}:
            _emit(
                Envelope(
                    name,
                    False,
                    error={
                        "code": "SESSION_REQUIRED",
                        "message": (
                            "This operation requires a live GDB session; persistent session "
                            "management is not implemented yet."
                        ),
                    },
                ),
                args.json,
            )
            return 2
        raise RuntimeError(f"{name} requires an active GDB session; use `edbg connect` first")
    except (ValueError, OSError, GDBError, OpenOCDError, PermissionError, RuntimeError) as exc:
        code = "AUTHORIZATION_REQUIRED" if isinstance(exc, PermissionError) else "OPERATION_FAILED"
        _emit(Envelope(name, False, error={"code": code, "message": str(exc)}), args.json)
        return 2


def main() -> None:
    raise SystemExit(run())
