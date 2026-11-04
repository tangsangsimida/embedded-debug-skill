"""Shared authorization predicates used at CLI and session service boundaries."""

from __future__ import annotations

AUTHORIZATION_POLICIES: dict[str, tuple[str, str, str]] = {
    "cancel": ("run", "Cancelling a GDB operation", "--authorize-run"),
    "halt": ("run", "Pausing target execution", "--authorize-run"),
    "continue": ("run", "Target execution", "--authorize-run"),
    "step": ("run", "Target execution", "--authorize-run"),
    "next": ("run", "Target execution", "--authorize-run"),
    "breakpoint-add": ("target", "Changing target breakpoints", "--authorize"),
    "breakpoint-remove": ("target", "Changing target breakpoints", "--authorize"),
    "reset": ("target", "reset", "--authorize"),
    "memory-write": ("target", "memory-write", "--authorize"),
    "flash": ("target", "flash", "--authorize"),
    "variable-evaluate": (
        "expression",
        "Evaluating an arbitrary GDB expression (may execute code or modify target state)",
        "--authorize-expression",
    ),
}


def require_authorized(action: str, authorized: bool, flag: str = "--authorize") -> None:
    if not authorized:
        raise PermissionError(f"{action} requires explicit {flag}")


def requires_authorization(operation: str) -> bool:
    return operation in AUTHORIZATION_POLICIES


def require_operation_authorized(
    operation: str,
    *,
    authorized_run: bool = False,
    authorized: bool = False,
    authorized_expression: bool = False,
) -> None:
    """Apply the shared policy for target-affecting GDB operations."""
    policy = AUTHORIZATION_POLICIES.get(operation)
    if policy is None:
        return
    kind, action, flag = policy
    approved = (
        authorized_run
        if kind == "run"
        else authorized_expression
        if kind == "expression"
        else authorized
    )
    require_authorized(action, approved, flag)
