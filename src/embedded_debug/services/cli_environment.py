"""Environment, toolchain, and project-inspection CLI handlers."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from .. import __version__
from ..build_info import build_info
from ..config import ProjectConfig
from ..discovery import discover_project
from ..doctor import doctor
from ..environment import (
    inspect_environment,
    install_user_cli,
    remediation_plan,
    repair_user_path,
    rollback_user_path,
)
from ..models import Envelope
from ..safety import require_authorized
from ..tool_archive import install_tool_archive, remove_tool_archive
from .audit import audit
from .output import emit


def handle_environment_command(
    name: str, args: argparse.Namespace, cfg: ProjectConfig
) -> int | None:
    if name == "environment-inspect":
        inspected = inspect_environment(
            {
                **{k: str(v) for k, v in asdict(cfg.tools).items()},
                **({"gdb": args.gdb} if args.gdb else {}),
                **({"openocd": args.openocd} if args.openocd else {}),
            }
        )
        emit(Envelope(name, True, data=inspected), args.json)
        return 0
    if name == "environment-plan":
        inspected = inspect_environment(
            {
                **{k: str(v) for k, v in asdict(cfg.tools).items()},
                **({"gdb": args.gdb} if args.gdb else {}),
                **({"openocd": args.openocd} if args.openocd else {}),
            }
        )
        emit(Envelope(name, True, data={"actions": remediation_plan(inspected)}), args.json)
        return 0
    if name == "environment-install-cli":
        require_authorized("Installing the Python CLI", args.authorize)
        audit("install-python-cli", f"version={__version__}")
        result = install_user_cli(args.source, authorized=True)
        emit(Envelope(name, True, data=result), args.json)
        return 0
    if name == "environment-install-tool-archive":
        require_authorized("Installing the reviewed isolated tool archive", args.authorize)
        audit("install-tool-archive", f"kind={args.kind} sha256={args.sha256}")
        result = install_tool_archive(
            args.source, args.sha256, args.executable, args.kind, authorized=True
        )
        emit(Envelope(name, True, data=result), args.json)
        return 0
    if name == "environment-remove-tool-archive":
        require_authorized("Removing the isolated tool archive", args.authorize)
        audit("remove-tool-archive", f"sha256={args.sha256}")
        result = remove_tool_archive(args.sha256, authorized=True)
        emit(Envelope(name, True, data=result), args.json)
        return 0
    if name == "environment-path-add":
        result = repair_user_path(args.directory, shell=args.shell, authorized=bool(args.authorize))
        if result["changed"]:
            audit("user-path-add", f"{result['shell']}:{result['directory']}")
        emit(Envelope(name, True, data=result), args.json)
        return 0
    if name == "environment-path-remove":
        result = rollback_user_path(shell=args.shell, authorized=bool(args.authorize))
        if result["changed"]:
            audit("user-path-remove", f"{result['shell']}:{result['file']}")
        emit(Envelope(name, True, data=result), args.json)
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
        emit(env, args.json)
        return 0
    if name == "build-info":
        emit(Envelope(name, True, data=build_info()), args.json)
        return 0
    if name == "discover":
        emit(Envelope(name, True, data=discover_project()), args.json)
        return 0
    if name == "config-validate":
        emit(
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
        emit(
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
    return None
