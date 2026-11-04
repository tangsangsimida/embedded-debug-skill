"""Resolve user project settings into a persistent GDB session request."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..config import ProjectConfig
from ..discovery import discover_project, openocd_launch_options
from ..session_store import daemon_identity_matches, read_session


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
