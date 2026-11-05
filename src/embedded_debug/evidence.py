"""Opt-in, append-only evidence records for Agent debugging traces."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import portalocker

from .models import Envelope
from .session_store import current_session_name


def _authorization(command: str) -> str:
    if command in {"halt", "continue", "step", "next"}:
        return "run"
    if command in {"breakpoint-add", "breakpoint-remove"}:
        return "breakpoint"
    if command == "memory-write":
        return "write"
    if command == "flash":
        return "flash"
    if command == "reset":
        return "reset"
    # The envelope cannot distinguish a simple variable path from an authorized
    # arbitrary GDB expression. Keep this explicit rather than inferring safety.
    if command == "variable-read":
        return "unknown"
    return "none"


def evidence_record(envelope: Envelope) -> dict[str, Any]:
    """Normalize one CLI envelope without upgrading unknown facts."""

    task_id = os.environ.get("EDBG_TASK_ID")
    step_id = os.environ.get("EDBG_STEP_ID")
    return {
        "step": step_id or envelope.command,
        "operation": envelope.command,
        "task_id": task_id,
        "session": current_session_name(),
        "source": "cli-json",
        "observed_at": envelope.timestamp,
        "success": envelope.success,
        "target_state_before": envelope.target_state_before,
        "target_state_after": envelope.target_state,
        "authorization": _authorization(envelope.command),
        "authorization_source": os.environ.get("EDBG_AUTHORIZATION_SOURCE", "unknown"),
        "observations": envelope.data if envelope.success else [],
        "diagnostics": envelope.diagnostics
        + ([envelope.error] if envelope.error is not None else []),
        "raw_artifact": None,
    }


def append_evidence(envelope: Envelope) -> None:
    """Append a record when ``EDBG_EVIDENCE_FILE`` is explicitly configured.

    Evidence recording is observability and must not make an otherwise valid
    debug operation fail. Write failures are surfaced on stderr and never
    swallowed silently.
    """

    configured = os.environ.get("EDBG_EVIDENCE_FILE")
    if not configured:
        return
    path = Path(configured).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = evidence_record(envelope)
        artifact_dir = os.environ.get("EDBG_EVIDENCE_ARTIFACT_DIR")
        if artifact_dir:
            artifact_root = Path(artifact_dir).expanduser()
            artifact_root.mkdir(parents=True, exist_ok=True)
            artifact_name = f"{envelope.command}-{uuid4().hex}.json"
            artifact_path = artifact_root / artifact_name
            artifact_path.write_text(
                json.dumps(envelope.as_dict(), ensure_ascii=False, default=str) + "\n",
                encoding="utf-8",
            )
            record["raw_artifact"] = artifact_name
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with portalocker.Lock(str(path), mode="a", timeout=5, encoding="utf-8") as stream:
            stream.write(line)
    except (OSError, portalocker.exceptions.LockException, ValueError) as exc:
        print(f"Evidence recording failed for {path}: {exc}", file=sys.stderr)
