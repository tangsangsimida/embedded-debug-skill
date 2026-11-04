"""Stable human and JSON output helpers for CLI commands."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..evidence import append_evidence
from ..models import Envelope
from ..platforms import get_platform


@dataclass
class ExternalResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def emit(envelope: Envelope, as_json: bool) -> None:
    append_evidence(envelope)
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


def run_external(argv: Sequence[str], cwd: Path, as_json: bool, timeout: float) -> ExternalResult:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("External command timeout must be a finite positive number")
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=stdout_file,
            stderr=stderr_file,
            **get_platform().creation_options(),
        )
        identity = get_platform().process_identity(process.pid)
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            if identity is not None:
                current_identity = get_platform().process_identity(process.pid)
                # A launcher can exec the actual executable after Popen returns;
                # PID, creation time and user still identify the same process.
                if (
                    current_identity is not None
                    and current_identity.created == identity.created
                    and current_identity.username == identity.username
                ):
                    get_platform().terminate_process_tree(
                        process.pid, expected_identity=current_identity
                    )
            if process.poll() is None:
                process.kill()
            process.wait()
            if isinstance(exc, KeyboardInterrupt):
                raise
            timed_out = True
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", errors="replace")
        stderr = stderr_file.read().decode("utf-8", errors="replace")
    result = ExternalResult(process.returncode, stdout, stderr, timed_out)
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
