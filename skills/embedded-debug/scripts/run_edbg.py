#!/usr/bin/env python3
"""Run the CLI at the Skill's pinned version; never install without an explicit flag."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PINNED_VERSION = "0.1.0"
SOURCE = "git+https://github.com/tangsangsimida/embedded-debug-skill.git@c838beac5a047637fbd7cd869ecaa6c9c6456310"
PINNED_SOURCE_DIGEST = "7f48ae245b2d500b949f774ba3131e11eff37cef05e16c845e17c0939f8a6e8b"


def _matches_build_info(command: Sequence[str]) -> bool:
    try:
        result = subprocess.run(
            [*command, "--json", "build-info"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        data = json.loads(result.stdout)["data"] if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, TypeError):
        return False
    return (
        data.get("version") == PINNED_VERSION and data.get("source_digest") == PINNED_SOURCE_DIGEST
    )


def _cached_cli() -> Path:
    override = os.environ.get("EDBG_CACHE_HOME") or os.environ.get("XDG_CACHE_HOME")
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Caches"
    else:
        base = Path.home() / ".cache"
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    command = "edbg.exe" if sys.platform == "win32" else "edbg"
    return base / "embedded-debug-skill" / PINNED_VERSION / scripts / command


def _venv_python(cli_path: Path) -> Path:
    root = cli_path.parent.parent
    if sys.platform == "win32":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _run_cached(cli: Path, args: Sequence[str]) -> int:
    try:
        result = subprocess.run(
            [str(cli), "--version"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return 2
    if result.returncode != 0 or result.stdout.strip() != PINNED_VERSION:
        print("Cached EmbeddedDebug CLI failed its pinned version check.", file=sys.stderr)
        return 2
    if not _matches_build_info([str(cli)]):
        print("Cached CLI source does not match this Skill candidate.", file=sys.stderr)
        return 2
    return subprocess.run([str(cli), *args]).returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--install-python-cli", action="store_true")
    options, remainder = parser.parse_known_args(argv)

    existing_cli = shutil.which("edbg")
    if existing_cli:
        try:
            version_result = subprocess.run(
                [existing_cli, "--version"], capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"Existing edbg command could not be checked: {exc}", file=sys.stderr)
            return 2
        existing_version = version_result.stdout.strip()
        if version_result.returncode != 0 or existing_version != PINNED_VERSION:
            print(
                f"Existing edbg version {existing_version or 'unknown'} does not match "
                f"Skill {PINNED_VERSION}; update or roll back one of them.",
                file=sys.stderr,
            )
            return 2
        if not _matches_build_info([existing_cli]):
            print("Existing edbg source does not match this Skill candidate.", file=sys.stderr)
            return 2
        return subprocess.run([existing_cli, *remainder]).returncode

    try:
        installed = importlib.metadata.version("embedded-debug-skill")
    except importlib.metadata.PackageNotFoundError:
        installed = None

    if installed == PINNED_VERSION:
        module = [sys.executable, "-m", "embedded_debug.cli"]
        if not _matches_build_info(module):
            print("Installed CLI source does not match this Skill candidate.", file=sys.stderr)
            return 2
        return subprocess.run([*module, *remainder]).returncode
    if installed is not None:
        print(
            f"EmbeddedDebug CLI version {installed} does not match Skill {PINNED_VERSION}; "
            "update or roll back one of them.",
            file=sys.stderr,
        )
        return 2
    cached_cli = _cached_cli()
    if cached_cli.is_file():
        return _run_cached(cached_cli, remainder)
    if not options.install_python_cli:
        print(
            "EmbeddedDebug CLI is not installed. After explicit user authorization, run:\n"
            f"  {sys.executable} skills/embedded-debug/scripts/run_edbg.py "
            "--install-python-cli doctor --json",
            file=sys.stderr,
        )
        return 2

    if shutil.which("uvx"):
        command = ["uvx", "--from", SOURCE, "edbg"]
        if not _matches_build_info(command):
            print(
                "Pinned uvx package does not match this Skill source fingerprint.", file=sys.stderr
            )
            return 2
        command.extend(remainder)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.stdout:
            print(result.stdout, end="")
        return result.returncode
    # The interpreter running this launcher is the environment the user chose.
    # Prefer it over another python3 earlier in PATH (which may lack venv).
    python = sys.executable
    interpreter_version = (sys.version_info.major, sys.version_info.minor)
    if interpreter_version < (3, 11):
        print(
            "Python 3.11+ is required to install the CLI; current interpreter is "
            f"{sys.version.split()[0]}. Rerun with a supported Python interpreter.",
            file=sys.stderr,
        )
        return 2
    cached_cli.parent.mkdir(parents=True, exist_ok=True)
    create = subprocess.run(
        [python, "-m", "venv", str(cached_cli.parent.parent)],
        capture_output=True,
        text=True,
        check=False,
    )
    if create.returncode:
        print(create.stderr or "Could not create an isolated Python environment.", file=sys.stderr)
        print(
            "Install Python 3.11+ with venv support, or install uv and retry. "
            "No system toolchain or shell configuration was changed.",
            file=sys.stderr,
        )
        return create.returncode
    venv_python = _venv_python(cached_cli)
    install = subprocess.run(
        [str(venv_python), "-m", "pip", "install", SOURCE],
        capture_output=True,
        text=True,
        check=False,
    )
    if install.stdout:
        print(install.stdout, end="", file=sys.stderr)
    if install.stderr:
        print(install.stderr, end="", file=sys.stderr)
    if install.returncode:
        print(
            "Pinned CLI installation failed. Check Python venv/pip support and network access; "
            "system toolchains were not changed.",
            file=sys.stderr,
        )
        return install.returncode
    if not _matches_build_info([str(cached_cli)]):
        print("Installed CLI source does not match this Skill candidate.", file=sys.stderr)
        return 2
    return _run_cached(cached_cli, remainder)


if __name__ == "__main__":
    raise SystemExit(main())
