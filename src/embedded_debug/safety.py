"""Shared authorization predicates used at CLI and session service boundaries."""

from __future__ import annotations


def require_authorized(action: str, authorized: bool, flag: str = "--authorize") -> None:
    if not authorized:
        raise PermissionError(f"{action} requires explicit {flag}")
