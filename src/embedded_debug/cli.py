"""Thin CLI entry point for domain-specific command handlers."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .cli_parser import parser
from .config import ProjectConfig, discover_config, load_config
from .models import Envelope
from .services.cli_debug import handle_debug_command
from .services.cli_environment import handle_environment_command
from .services.cli_sessions import handle_session_command
from .services.output import emit
from .session_store import set_session_name


def _config(args: argparse.Namespace) -> ProjectConfig:
    path = args.config or discover_config()
    return load_config(path) if path else ProjectConfig()


def run(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    set_session_name(args.session)
    name = getattr(args, "canonical_command", args.command)
    try:
        cfg = _config(args)
        for handler in (
            handle_environment_command,
            handle_session_command,
            handle_debug_command,
        ):
            result = handler(name, args, cfg)
            if result is not None:
                return result
        raise RuntimeError(f"Unsupported command: {name}")
    except (ValueError, OSError, RuntimeError, PermissionError) as exc:
        code = "AUTHORIZATION_REQUIRED" if isinstance(exc, PermissionError) else "OPERATION_FAILED"
        emit(Envelope(name, False, error={"code": code, "message": str(exc)}), args.json)
        return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
