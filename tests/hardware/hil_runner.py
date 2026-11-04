"""Opt-in, user-configured hardware-in-the-loop debug sequence.

This runner uses the project's existing GDB/OpenOCD setup. It does not select a
target model or invent OpenOCD configuration. Run control is disabled unless both
the config and a second explicit environment opt-in enable it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ValueError(f"Hardware test config does not exist: {config_path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid hardware test JSON config: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("Hardware test config must be a JSON object")
    if not isinstance(config.get("connect_args", []), list) or not all(
        isinstance(value, str) for value in config.get("connect_args", [])
    ):
        raise ValueError("connect_args must be a list of strings")
    if not isinstance(config.get("expressions", []), list) or not all(
        isinstance(value, str) for value in config.get("expressions", [])
    ):
        raise ValueError("expressions must be a list of strings")
    for name in ("run_control", "authorize_run_control", "authorize_breakpoints"):
        if name in config and not isinstance(config[name], bool):
            raise ValueError(f"{name} must be a boolean")
    for name, default, maximum in (("command_timeout", 30.0, 300.0), ("run_timeout", 3.0, 60.0)):
        value = config.get(name, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not 0 < value <= maximum
        ):
            raise ValueError(f"{name} must be greater than 0 and at most {maximum:g} seconds")
    return config


def _invoke(config: dict[str, Any], *args: str) -> dict[str, Any]:
    cli = str(config.get("cli", "edbg"))
    session = str(config["session"])
    global_args = config.get("global_args", [])
    if not isinstance(global_args, list) or not all(isinstance(arg, str) for arg in global_args):
        raise ValueError("global_args must be a list of strings")
    command = [cli, *global_args, "--json", "--session", session, *args]
    result = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=float(config.get("command_timeout", 30)),
        check=False,
    )
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{args!r} returned non-JSON (exit {result.returncode}): {result.stderr[-2000:]}"
        ) from exc
    if result.returncode or not response.get("success"):
        raise AssertionError(
            f"{args!r} failed with exit {result.returncode}: "
            f"{response.get('error')}; stderr={result.stderr[-2000:]}"
        )
    return response


def run_hil(config: dict[str, Any]) -> dict[str, Any]:
    """Exercise an existing target setup and always close the EDBG session."""
    config = dict(config)
    config.setdefault("session", f"hil-{uuid.uuid4().hex[:12]}")
    connect_args = config.get("connect_args", [])
    elf_arg: str | None = None
    for index, argument in enumerate(connect_args):
        if argument == "--elf":
            if index + 1 >= len(connect_args):
                raise ValueError("--elf in connect_args requires a file path")
            elf_arg = connect_args[index + 1]
            break
        if argument.startswith("--elf="):
            elf_arg = argument.partition("=")[2]
            break
    if elf_arg:
        elf = Path(elf_arg).expanduser()
        if not elf.is_file():
            raise ValueError(f"Configured ELF does not exist: {elf}")
    elif config.get("expressions"):
        raise ValueError("Variable checks require --elf in connect_args")

    run_control = config.get("run_control", False)
    if not isinstance(run_control, bool):
        raise ValueError("run_control must be a boolean")
    for name in ("authorize_run_control", "authorize_breakpoints"):
        if name in config and not isinstance(config[name], bool):
            raise ValueError(f"{name} must be a boolean")
    breakpoints = config.get("breakpoints", [])
    if not isinstance(breakpoints, list) or not all(isinstance(x, str) for x in breakpoints):
        raise ValueError("breakpoints must be a list of source locations")
    if breakpoints and not config.get("authorize_breakpoints", False):
        raise PermissionError("breakpoint operations require authorize_breakpoints=true")
    if run_control and (
        not config.get("authorize_run_control", False)
        or os.environ.get("EDBG_HARDWARE_RUN_CONTROL") != "1"
    ):
        raise PermissionError(
            "halt/step/continue require authorize_run_control=true and "
            "EDBG_HARDWARE_RUN_CONTROL=1"
        )

    results: dict[str, Any] = {}
    breakpoint_numbers: list[str] = []
    connected = False
    connection_attempted = False
    current = _invoke(config, "session", "status")
    if isinstance(current.get("data"), dict) and current["data"].get("active"):
        raise RuntimeError(f"Refusing to reuse already active HIL session {config['session']!r}")
    try:
        connection_attempted = True
        results["connect"] = _invoke(config, "connect", *connect_args)
        connected = True
        results["status"] = _invoke(config, "status")
        _require_state(results["status"], "halted", "before stop-mode inspection")
        results["registers"] = _invoke(config, "registers")
        results["backtrace"] = _invoke(config, "backtrace")
        results["variables"] = {
            expression: _invoke(config, "variable", "read", expression)
            for expression in config.get("expressions", [])
        }
        for location in breakpoints:
            added = _invoke(config, "breakpoint", "add", location)
            results.setdefault("breakpoints", []).append(added)
            data = added.get("data")
            breakpoint_data = data.get("bkpt", data) if isinstance(data, dict) else None
            number = (
                breakpoint_data.get("number")
                if isinstance(breakpoint_data, dict)
                else None
            )
            if number is not None:
                breakpoint_numbers.append(str(number))
        if run_control:
            _invoke(config, "halt", "--authorize-run")
            results["step"] = _invoke(config, "step", "--authorize-run")
            _wait_for_state(config, "halted", "after step")
            _invoke(config, "continue", "--authorize-run")
            deadline = time.monotonic() + float(config.get("run_timeout", 3))
            while time.monotonic() < deadline:
                status = _invoke(config, "status")
                if status.get("target_state") == "running":
                    results["running_observed"] = status
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("GDB did not report running after continue")
            _invoke(config, "halt", "--authorize-run")
            results["halted"] = _wait_for_state(config, "halted", "after halt")
            _require_state(results["halted"], "halted", "after halt")
        return results
    finally:
        if connection_attempted:
            cleanup_errors: list[str] = []
            if connected and run_control and os.environ.get("EDBG_HARDWARE_RUN_CONTROL") == "1":
                try:
                    _invoke(config, "halt", "--authorize-run")
                except (AssertionError, OSError, subprocess.SubprocessError) as exc:
                    cleanup_errors.append(f"final halt failed: {exc}")
            for number in breakpoint_numbers if connected else []:
                try:
                    _invoke(config, "breakpoint", "remove", number)
                except (AssertionError, OSError, subprocess.SubprocessError) as exc:
                    cleanup_errors.append(f"breakpoint {number} cleanup failed: {exc}")
            results["cleanup"] = _invoke(config, "session", "stop")
            stopped = results["cleanup"].get("data", {}).get("stopped")
            if connected and not stopped:
                raise AssertionError("EmbeddedDebug session did not confirm cleanup")
            if cleanup_errors:
                raise AssertionError("HIL cleanup issues: " + "; ".join(cleanup_errors))


def _require_state(response: dict[str, Any], state: str, context: str) -> None:
    observed = response.get("target_state")
    if observed != state:
        raise AssertionError(f"Expected target state {state!r} {context}; got {observed!r}")


def _wait_for_state(config: dict[str, Any], state: str, context: str) -> dict[str, Any]:
    deadline = time.monotonic() + float(config.get("run_timeout", 3))
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _invoke(config, "status")
        if last.get("target_state") == state:
            return last
        time.sleep(0.05)
    _require_state(last, state, context)
    return last
