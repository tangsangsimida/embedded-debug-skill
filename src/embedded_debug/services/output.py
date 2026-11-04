"""Stable human and JSON output helpers for CLI commands."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from ..models import Envelope


def emit(envelope: Envelope, as_json: bool) -> None:
    if as_json:
        print(json.dumps(envelope.as_dict(), ensure_ascii=False, default=str))
        if envelope.error:
            print(f"{envelope.error['code']}: {envelope.error['message']}", file=sys.stderr)
        return
    label = "OK" if envelope.success else "ERROR"
    print(f"[{label}] {envelope.command}")
    if envelope.data is not None:
        print(json.dumps(envelope.data, ensure_ascii=False, indent=2, default=str))
    if envelope.error:
        print(f"{envelope.error['code']}: {envelope.error['message']}", file=sys.stderr)
    for diagnostic in envelope.diagnostics:
        print(diagnostic, file=sys.stderr if not envelope.success else sys.stdout)


def run_external(
    argv: Sequence[str], cwd: Path, as_json: bool
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
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
