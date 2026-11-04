"""Central authorization gates and append-only audit recording."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from ..platforms import get_platform
from ..safety import require_authorized, require_operation_authorized, requires_authorization
from ..session_store import current_session_name


def authorize(
    action: str, target: str, *, authorized: bool, flag: str = "--authorize"
) -> None:
    """Reject unapproved state changes and audit every approved operation."""
    require_authorized(action, authorized, flag)
    audit(action, target)


def authorize_operation(
    operation: str,
    target: str,
    *,
    authorized_run: bool = False,
    authorized: bool = False,
    authorized_expression: bool = False,
) -> None:
    """Enforce and audit one target-affecting operation from the shared policy."""
    require_operation_authorized(
        operation,
        authorized_run=authorized_run,
        authorized=authorized,
        authorized_expression=authorized_expression,
    )
    if requires_authorization(operation):
        audit(operation, target)


def audit(action: str, target: str) -> None:
    path = Path(os.environ.get("EDBG_AUDIT_LOG", get_platform().state_dir() / "audit.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session": current_session_name(),
        "action": action,
        "target": target,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
