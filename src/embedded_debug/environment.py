"""System-first tool discovery and safe, user-scoped CLI installation helpers.

The environment manager never installs or rewrites the embedded system toolchain.
Its installer is limited to the Python CLI in an isolated, versioned virtualenv.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path

from . import __version__
from .build_info import build_info
from .doctor import inspect_gdb_mi, inspect_tool

DEFAULT_TOOLS = {
    "python": ("Python", sys.executable, True),
    "gcc": ("ARM GCC", "arm-none-eabi-gcc", False),
    "gdb": ("ARM GDB", "arm-none-eabi-gdb", False),
    "openocd": ("OpenOCD", "openocd", False),
    "objcopy": ("ARM Objcopy", "arm-none-eabi-objcopy", False),
    "size": ("ARM Size", "arm-none-eabi-size", False),
}


def inspect_environment(overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """Inspect known tools, honoring explicit overrides before PATH discovery."""
    selected = overrides or {}
    tools: dict[str, dict[str, Any]] = {}
    for key, (label, default, required) in DEFAULT_TOOLS.items():
        executable = selected.get(key, default)
        result = inspect_tool(label, executable, required=required)
        result["key"] = key
        result["role"] = "required" if required else "optional"
        tools[key] = result
    gdb = tools["gdb"]
    gdb["mi2"] = inspect_gdb_mi(selected.get("gdb", DEFAULT_TOOLS["gdb"][1]))
    missing_required = [
        key for key, item in tools.items() if item["required"] and item["status"] != "available"
    ]
    missing_debug = [key for key in ("gdb", "openocd") if tools[key]["status"] != "available"]
    return {
        "tools": tools,
        "cli_ready": not missing_required,
        "debug_tools_ready": not missing_debug and gdb["mi2"] == "supported",
        "missing_required": missing_required,
        "missing_debug_tools": missing_debug,
    }


def remediation_plan(
    inspection: dict[str, Any], *, shell: str | None = None
) -> list[dict[str, str]]:
    """Return non-mutating next steps; system packages are never installed here."""
    items: list[dict[str, str]] = []
    selected_shell = shell or Path(os.environ.get("SHELL", "")).name
    for key, item in inspection.get("tools", {}).items():
        if item.get("status") == "available":
            continue
        executable = str(item.get("executable", key))
        if key in {"gcc", "gdb", "objcopy", "size"}:
            message = "Install/select an ARM GNU Toolchain distribution containing this tool."
        elif key == "openocd":
            message = (
                "Install/select an OpenOCD build suitable for the project's adapter "
                "and target scripts."
            )
        else:
            message = "Install Python 3.11 or newer."
        if selected_shell == "fish":
            path_example = "fish_add_path /path/to/tool/bin"
        elif selected_shell in {"bash", "zsh"}:
            path_example = 'export PATH="/path/to/tool/bin:$PATH"'
        elif os.name == "nt":
            path_example = '$env:Path = "C:\\path\\to\\tool\\bin;$env:Path"'
        else:
            path_example = "Add the tool's bin directory to PATH using your shell or OS settings."
        items.append({"tool": executable, "action": message, "path_guidance": path_example})
    return items


def install_user_cli(source: str, *, authorized: bool = False) -> dict[str, str]:
    """Install the Python CLI from an explicit source into an isolated user cache.

    Installs are versioned. A failed install removes only its temporary directory;
    prior versions are left intact and can be selected again by the caller.
    """
    if not authorized:
        raise PermissionError("Python CLI installation requires explicit authorization")
    if not source.strip():
        raise ValueError("An explicit package source is required")
    parent = Path(user_cache_path("embedded-debug-skill")) / "cli"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / __version__
    python_name = "python.exe" if os.name == "nt" else "python"
    bin_name = "Scripts" if os.name == "nt" else "bin"
    executable_name = "edbg.exe" if os.name == "nt" else "edbg"
    if (destination / bin_name / executable_name).is_file():
        _verify_cli(destination / bin_name / executable_name)
        return {"version": __version__, "path": str(destination / bin_name / executable_name)}
    if destination.exists():
        raise RuntimeError(
            f"A partial CLI install already exists at {destination}; remove it explicitly "
            "after reviewing its contents before retrying"
        )
    try:
        try:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                venv.EnvBuilder(with_pip=True, clear=True).create(destination)
            installer = [str(destination / bin_name / python_name), "-m", "pip"]
        except (subprocess.CalledProcessError, SystemExit):
            # Some OS distributions separate ensurepip from the Python runtime.
            # Use the current interpreter's pip targeting support without
            # installing anything outside this newly created virtualenv.
            shutil.rmtree(destination, ignore_errors=True)
            venv.EnvBuilder(with_pip=False, clear=True).create(destination)
            installer = [
                sys.executable,
                "-m",
                "pip",
                "--python",
                str(destination / bin_name / python_name),
            ]
        install = subprocess.run(
            [
                *installer,
                "install",
                "--disable-pip-version-check",
                "--no-input",
                source,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        if install.returncode:
            details = (install.stderr or install.stdout).strip()[-2000:]
            raise RuntimeError(
                "Could not install into the isolated Python environment. Ensure the "
                "selected Python has venv support or a pip version with --python support. "
                f"{details}"
            )
        executable = destination / bin_name / executable_name
        _verify_cli(executable)
        return {"version": __version__, "path": str(destination / bin_name / executable_name)}
    except SystemExit as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RuntimeError(
            "Python could not create the isolated virtualenv; check venv support "
            "for the selected interpreter"
        ) from exc
    except subprocess.SubprocessError as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RuntimeError(
            "Python could not create or verify the isolated CLI environment; "
            "check venv/pip support for the selected interpreter"
        ) from exc
    except (OSError, RuntimeError):
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _verify_cli(executable: Path) -> None:
    try:
        check = subprocess.run(
            [str(executable), "--json", "build-info"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Installed CLI could not be verified: {exc}") from exc
    try:
        info = json.loads(check.stdout)["data"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("Installed CLI returned invalid build metadata") from exc
    expected = build_info()
    if (
        check.returncode
        or info.get("version") != expected["version"]
        or info.get("source_digest") != expected["source_digest"]
    ):
        raise RuntimeError("Installed CLI version/source did not match the running Skill")


def current_tool_path(key: str, override: str | None = None) -> str | None:
    """Resolve one configured tool without embedding installation prefixes."""
    if key not in DEFAULT_TOOLS:
        raise ValueError(f"Unknown tool key: {key}")
    candidate = override or DEFAULT_TOOLS[key][1]
    return shutil.which(candidate) or (
        str(Path(candidate).resolve()) if Path(candidate).is_file() else None
    )
