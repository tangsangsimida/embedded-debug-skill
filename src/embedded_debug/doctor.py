"""System-first executable discovery and bounded tool diagnostics."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .discovery import discover_project
from .platforms import get_platform


def inspect_tool(
    name: str, executable: str, *, required: bool, timeout: float = 2.0
) -> dict[str, Any]:
    path = shutil.which(executable)
    if path is None and Path(executable).is_file():
        path = str(Path(executable).resolve())
    item: dict[str, Any] = {
        "name": name,
        "executable": executable,
        "required": required,
        "status": "missing",
        "path": path,
        "version": None,
    }
    if path is None:
        return item
    try:
        result = subprocess.run(
            get_platform().executable_argv(path, "--version"),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            item["status"] = "invalid"
            item["detail"] = (result.stderr or result.stdout).strip()[:500]
        else:
            item["status"] = "available"
            item["version"] = (result.stdout or result.stderr).splitlines()[0][:300]
    except (OSError, subprocess.TimeoutExpired) as exc:
        item["status"] = "invalid"
        item["detail"] = str(exc)
    return item


def inspect_gdb_mi(executable: str, timeout: float = 3.0) -> str:
    """Confirm MI2 from a bounded batch invocation without connecting to hardware."""
    path = shutil.which(executable)
    if path is None and Path(executable).is_file():
        path = str(Path(executable).resolve())
    if path is None:
        return "unknown"
    try:
        result = subprocess.run(
            get_platform().executable_argv(path, "--interpreter=mi2", "--quiet"),
            input="1-gdb-version\n2-gdb-exit\n",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if result.returncode == 0 and "1^done" in result.stdout:
        return "supported"
    return "unsupported"


def doctor(
    tool_paths: dict[str, str] | None = None,
    verbose: bool = False,
    elf: str | None = None,
) -> dict[str, Any]:
    paths = tool_paths or {}
    tools = [
        inspect_tool("Python", os.environ.get("PYTHON", sys.executable), required=True),
        inspect_tool("ARM GCC", paths.get("gcc", "arm-none-eabi-gcc"), required=False),
        inspect_tool("ARM GDB", paths.get("gdb", "arm-none-eabi-gdb"), required=False),
        inspect_tool("OpenOCD", paths.get("openocd", "openocd"), required=False),
        inspect_tool("ARM Objcopy", paths.get("objcopy", "arm-none-eabi-objcopy"), required=False),
        inspect_tool("ARM Size", paths.get("size", "arm-none-eabi-size"), required=False),
    ]
    gdb = next(item for item in tools if item["name"] == "ARM GDB")
    openocd = next(item for item in tools if item["name"] == "OpenOCD")
    gdb_mi = inspect_gdb_mi(paths.get("gdb", "arm-none-eabi-gdb"))
    gdb["mi2"] = gdb_mi
    for item in tools:
        item["role"] = "required" if item["required"] else "optional"
        if not verbose:
            item.pop("executable", None)
    project = discover_project()
    result: dict[str, Any] = {
        "tools": tools,
        "project": project,
        "cli_ready": all(t["status"] == "available" for t in tools if t["required"]),
        "debug_tools_ready": (
            gdb["status"] == "available"
            and gdb_mi == "supported"
            and openocd["status"] == "available"
        ),
        "hardware_connection": (
            "server_detected" if project["active_gdb_servers"] else "not_checked"
        ),
        "shell_guidance": _shell_guidance(),
    }
    if elf:
        elf_path = Path(elf).expanduser()
        result["elf"] = {
            "path": str(elf_path),
            "status": "available" if elf_path.is_file() else "missing",
        }
    return result


def _shell_guidance() -> str:
    if sys.platform == "win32":
        if os.environ.get("PSModulePath"):
            return '$env:Path = "C:\\path\\to\\toolchain\\bin;$env:Path"'
        if os.environ.get("COMSPEC"):
            return 'set "PATH=C:\\path\\to\\toolchain\\bin;%PATH%"'
        return "Add the toolchain bin directory to PATH in Windows Environment Variables."
    shell = Path(os.environ.get("SHELL", "")).name
    if shell == "fish":
        return "fish_add_path /path/to/toolchain/bin"
    if shell in {"bash", "zsh"}:
        return 'export PATH="/path/to/toolchain/bin:$PATH"'
    return "Add the toolchain bin directory to PATH using your shell or OS settings."
