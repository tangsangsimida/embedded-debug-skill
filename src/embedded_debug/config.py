"""Dependency-free TOML configuration loading and validation."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ToolConfig:
    gdb: str = "arm-none-eabi-gdb"
    openocd: str = "openocd"
    gcc: str = "arm-none-eabi-gcc"
    objcopy: str = "arm-none-eabi-objcopy"
    size: str = "arm-none-eabi-size"


@dataclass(slots=True)
class OpenOCDConfig:
    config: str | None = None
    interface: str | None = None
    target: str | None = None
    board: str | None = None
    commands: list[str] = field(default_factory=list)
    host: str = "127.0.0.1"
    gdb_port: int = 3333
    telnet_port: int = 4444
    tcl_port: int = 6666
    adapter_khz: int | None = None


@dataclass(slots=True)
class ProjectConfig:
    tools: ToolConfig = field(default_factory=ToolConfig)
    openocd: OpenOCDConfig = field(default_factory=OpenOCDConfig)
    elf: str | None = None
    build_command: list[str] | None = None
    build_dir: str = "build"
    config_path: Path | None = None


def load_config(path: Path) -> ProjectConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    allowed_top = {"tools", "openocd", "project", "build"}
    unknown = set(raw) - allowed_top
    if unknown:
        raise ValueError(f"unknown configuration section(s): {', '.join(sorted(unknown))}")
    tool_data = raw.get("tools", {})
    oc_data = raw.get("openocd", {})
    project = raw.get("project", {})
    build = raw.get("build", {})
    for name, value in (
        ("tools", tool_data),
        ("openocd", oc_data),
        ("project", project),
        ("build", build),
    ):
        if not isinstance(value, dict):
            raise ValueError(f"[{name}] must be a TOML table")
    _reject_unknown(tool_data, set(ToolConfig.__dataclass_fields__), "tools")
    _reject_unknown(oc_data, set(OpenOCDConfig.__dataclass_fields__), "openocd")
    _reject_unknown(project, {"elf"}, "project")
    _reject_unknown(build, {"command", "directory"}, "build")
    if "commands" in oc_data and (
        not isinstance(oc_data["commands"], list)
        or not all(isinstance(item, str) for item in oc_data["commands"])
    ):
        raise ValueError("openocd.commands must be an array of strings")
    if "command" in build and (
        not isinstance(build["command"], list)
        or not all(isinstance(item, str) for item in build["command"])
    ):
        raise ValueError("build.command must be an argument array of strings")
    if any(not isinstance(value, str) for value in tool_data.values()):
        raise ValueError("all [tools] values must be executable path strings")
    if any(not isinstance(project.get(key), str) for key in project if key == "elf"):
        raise ValueError("project.elf must be a path string")
    if "directory" in build and not isinstance(build["directory"], str):
        raise ValueError("build.directory must be a path string")
    if not isinstance(oc_data.get("host", "127.0.0.1"), str):
        raise ValueError("openocd.host must be a loopback address string")
    for port in ("gdb_port", "telnet_port", "tcl_port"):
        value = oc_data.get(port, getattr(OpenOCDConfig(), port))
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"openocd.{port} must be an integer port")
    oc = OpenOCDConfig(**oc_data)
    if any(not 1 <= port <= 65535 for port in (oc.gdb_port, oc.telnet_port, oc.tcl_port)):
        raise ValueError("OpenOCD ports must be between 1 and 65535")
    if oc.adapter_khz is not None and oc.adapter_khz <= 0:
        raise ValueError("openocd.adapter_khz must be positive")
    if oc.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("OpenOCD host must be loopback to avoid network exposure")
    cfg = ProjectConfig(
        ToolConfig(**tool_data),
        oc,
        project.get("elf"),
        build.get("command"),
        build.get("directory", "build"),
        path.resolve(),
    )
    return cfg


def discover_config(cwd: Path | None = None) -> Path | None:
    root = cwd or Path.cwd()
    env = os.environ.get("EDBG_CONFIG")
    candidates = [Path(env)] if env else []
    candidates.extend([root / "edbg.toml", root / ".edbg.toml"])
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _reject_unknown(data: dict[str, Any], allowed: set[str], section: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown key(s) in [{section}]: {', '.join(sorted(unknown))}")
