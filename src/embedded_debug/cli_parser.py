"""Argument parser and CLI syntax, independent from command execution."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import __version__
from .session_store import validate_session_name


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
    environment = command_parser("environment", help="inspect or safely remediate user tooling")
    environment_actions = environment.add_subparsers(dest="action", required=True)
    for action in ("inspect", "plan"):
        leaf = environment_actions.add_parser(action)
        leaf.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        leaf.set_defaults(canonical_command=f"environment-{action}")
    install_cli = environment_actions.add_parser("install-cli")
    install_cli.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    install_cli.add_argument("--source", required=True, help="explicit pip package source")
    install_cli.add_argument("--authorize", action="store_true")
    install_cli.set_defaults(canonical_command="environment-install-cli")
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
