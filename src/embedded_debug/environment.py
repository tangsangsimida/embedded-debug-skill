"""System-first tool discovery and safe, user-scoped CLI installation helpers.

The environment manager never installs or rewrites the embedded system toolchain.
Its installer is limited to the Python CLI in an isolated, versioned virtualenv.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
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

PATH_BLOCK_START = "# >>> embedded-debug-skill PATH >>>"
PATH_BLOCK_END = "# <<< embedded-debug-skill PATH <<<"
SHELL_RC_FILES = {
    "bash": ".bashrc",
    "zsh": ".zshrc",
    "fish": ".config/fish/config.fish",
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


def repair_user_path(
    directory: str | Path, *, shell: str | None = None, authorized: bool = False
) -> dict[str, Any]:
    """Add a directory to the selected user's shell PATH using a managed block.

    Only the current user's bash, zsh, or fish startup file is changed, and only
    after explicit authorization. The operation is idempotent and reversible via
    :func:`rollback_user_path`.
    """
    if not authorized:
        raise PermissionError("PATH repair requires explicit authorization")
    selected_shell = _selected_shell(shell)
    candidate = Path(directory).expanduser()
    if not candidate.is_absolute():
        raise ValueError("PATH directory must be an absolute path")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"PATH directory does not exist: {candidate}") from exc
    if not resolved.is_dir():
        raise ValueError(f"PATH entry is not a directory: {resolved}")

    rc_file = Path.home() / SHELL_RC_FILES[selected_shell]
    existing = _read_shell_file(rc_file)
    quoted = shlex.quote(str(resolved))
    if selected_shell == "fish":
        path_line = f"set -gx PATH {quoted} $PATH"
    else:
        path_line = f"export PATH={quoted}:\"$PATH\""
    original_final_newline = _managed_final_newline(existing)
    original_file_existed = (
        _managed_file_existed(existing) if PATH_BLOCK_START in existing else rc_file.exists()
    )
    block = (
        f"{PATH_BLOCK_START}\n"
        f"# original-file-existed: {'true' if original_file_existed else 'false'}\n"
        f"# original-final-newline: {'true' if original_final_newline else 'false'}\n"
        f"{path_line}\n{PATH_BLOCK_END}"
    )
    updated, changed = _replace_managed_block(existing, block)
    if changed:
        _atomic_write_user_file(rc_file, updated)
    return {
        "shell": selected_shell,
        "file": str(rc_file),
        "directory": str(resolved),
        "changed": changed,
    }


def rollback_user_path(*, shell: str | None = None, authorized: bool = False) -> dict[str, Any]:
    """Remove only EmbeddedDebug-Skill's managed PATH block from a shell file."""
    if not authorized:
        raise PermissionError("PATH rollback requires explicit authorization")
    selected_shell = _selected_shell(shell)
    rc_file = Path.home() / SHELL_RC_FILES[selected_shell]
    existing = _read_shell_file(rc_file)
    original_file_existed = (
        _managed_file_existed(existing) if PATH_BLOCK_START in existing else True
    )
    updated, changed = _remove_managed_block(existing)
    if changed:
        if not original_file_existed and not updated:
            rc_file.unlink()
        else:
            _atomic_write_user_file(rc_file, updated)
    return {"shell": selected_shell, "file": str(rc_file), "changed": changed}


def _selected_shell(shell: str | None) -> str:
    selected = (shell or Path(os.environ.get("SHELL", "")).name).casefold()
    if selected not in SHELL_RC_FILES:
        raise ValueError("Select a supported shell explicitly: bash, zsh, or fish")
    return selected


def _read_shell_file(path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"Refusing to modify a symlinked shell startup file: {path}")
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Could not read shell startup file {path}: {exc}") from exc


def _replace_managed_block(existing: str, block: str) -> tuple[str, bool]:
    start = existing.find(PATH_BLOCK_START)
    end = existing.find(PATH_BLOCK_END, start + len(PATH_BLOCK_START)) if start >= 0 else -1
    if start >= 0 and end >= 0:
        end += len(PATH_BLOCK_END)
        if existing[start:end] == block:
            return existing, False
    current, _ = _remove_managed_block(existing)
    separator = "" if not current or current.endswith("\n") else "\n"
    updated = f"{current}{separator}{block}\n"
    return updated, True


def _remove_managed_block(existing: str) -> tuple[str, bool]:
    start_count = existing.count(PATH_BLOCK_START)
    end_count = existing.count(PATH_BLOCK_END)
    if start_count != end_count or start_count > 1:
        raise ValueError("Malformed or duplicate EmbeddedDebug PATH markers; refusing edit")
    if not start_count:
        return existing, False
    start = existing.index(PATH_BLOCK_START)
    end = existing.index(PATH_BLOCK_END, start) + len(PATH_BLOCK_END)
    line_start = existing.rfind("\n", 0, start) + 1
    if end < len(existing) and existing[end] == "\n":
        end += 1
    updated = existing[:line_start] + existing[end:]
    block = existing[start:end]
    if "# original-final-newline: false\n" in block and not updated[line_start:]:
        if line_start and updated[:line_start].endswith("\n"):
            updated = updated[:line_start - 1]
    return updated, True


def _managed_final_newline(existing: str) -> bool:
    if PATH_BLOCK_START not in existing:
        return existing.endswith("\n")
    if "# original-final-newline: true\n" in existing:
        return True
    if "# original-final-newline: false\n" in existing:
        return False
    raise ValueError("Managed PATH block metadata is missing; refusing to replace it")


def _managed_file_existed(existing: str) -> bool:
    if "# original-file-existed: true\n" in existing:
        return True
    if "# original-file-existed: false\n" in existing:
        return False
    raise ValueError("Malformed or incomplete PATH block file metadata; refusing to edit")


def _atomic_write_user_file(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise ValueError(f"Refusing to replace a symlinked shell startup file: {path}")
    old_mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".edbg-path-", delete=False
        ) as stream:
            temporary = stream.name
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, old_mode)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise RuntimeError(f"Could not update user shell file {path}: {exc}") from exc


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
