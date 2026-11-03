#!/usr/bin/env python3
"""Run the CLI at the Skill's pinned version; never install without an explicit flag."""

from __future__ import annotations

import argparse
import importlib.metadata
import shutil
import subprocess
import sys
from collections.abc import Sequence

PINNED_VERSION = "0.1.0"
SOURCE = "git+https://github.com/tangsangsimida/embedded-debug-skill.git@v0.1.0"


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
        return subprocess.run([existing_cli, *remainder]).returncode

    try:
        installed = importlib.metadata.version("embedded-debug-skill")
    except importlib.metadata.PackageNotFoundError:
        installed = None

    if installed == PINNED_VERSION:
        return subprocess.run([sys.executable, "-m", "embedded_debug.cli", *remainder]).returncode
    if installed is not None:
        print(
            f"EmbeddedDebug CLI version {installed} does not match Skill {PINNED_VERSION}; "
            "update or roll back one of them.",
            file=sys.stderr,
        )
        return 2
    if not options.install_python_cli:
        print(
            "EmbeddedDebug CLI is not installed. After explicit user authorization, run:\n"
            "  python3 skills/embedded-debug/scripts/run_edbg.py "
            "--install-python-cli doctor --json",
            file=sys.stderr,
        )
        return 2

    if shutil.which("uvx"):
        command = ["uvx", "--from", SOURCE, "edbg", *remainder]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.stdout:
            print(result.stdout, end="")
        return result.returncode
    if shutil.which("python3"):
        install = subprocess.run(
            [
                "python3",
                "-m",
                "pip",
                "install",
                "--user",
                "--no-deps",
                SOURCE,
            ],
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
                "Python package installation failed. Use an isolated package manager or a "
                "virtual environment; system toolchains were not changed.",
                file=sys.stderr,
            )
            return install.returncode
        return subprocess.run(["python3", "-m", "embedded_debug.cli", *remainder]).returncode
    print("Python 3 is unavailable; install Python 3.11+ and retry.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
