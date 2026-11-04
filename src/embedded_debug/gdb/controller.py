"""GDB operations and response-driven capability detection."""

from __future__ import annotations

import re
from typing import Any

from ..models import CapabilitySet
from .mi_transport import MITransport


class GDBController:
    _VARIABLE_PATH = re.compile(
        r"[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::)[A-Za-z_][A-Za-z0-9_]*)*\Z"
    )

    def __init__(self, transport: MITransport) -> None:
        self.mi = transport
        self.architecture: str | None = None
        self.registers: list[str] = []
        self.capabilities = CapabilitySet()

    def connect(
        self, host: str = "127.0.0.1", port: int = 3333, elf: str | None = None
    ) -> dict[str, Any]:
        self.mi.start(elf)
        # In synchronous MI mode GDB cannot process -exec-interrupt while the
        # inferior is running. Require asynchronous command processing.
        self.mi.command("-gdb-set mi-async on")
        self.mi.command(f"-target-select remote {host}:{port}", timeout=30)
        # target-select does not guarantee a halted state; async stop records update it.
        self.detect_capabilities()
        return {
            "architecture": self.architecture,
            "connection": "connected",
            "capabilities": self.capabilities.__dict__
            if hasattr(self.capabilities, "__dict__")
            else {name: getattr(self.capabilities, name) for name in self.capabilities.__slots__},
        }

    def detect_capabilities(self) -> None:
        try:
            result = self.mi.command("-gdb-show architecture")
            self.architecture = str((result.results or {}).get("value", "unknown"))
        except Exception:
            self.architecture = None
        try:
            result = self.mi.command("-data-list-register-names")
            self.registers = list((result.results or {}).get("register-names", []))
            if self.registers and self.mi.state == "halted":
                self.mi.command("-data-list-register-values x")
                self.capabilities.register_read = "supported"
        except Exception:
            self.capabilities.register_read = "unknown"
        # Memory access is not probed at an arbitrary address: reads can fault or have
        # side effects. RTOS awareness is target-specific, and GDB thread groups alone
        # cannot prove it. Breakpoint list only proves the generic MI command exists.
        for command, attr in (("-break-list", "breakpoints"),):
            try:
                self.mi.command(command)
                # A valid MI operation does not prove target-side resource support.
                setattr(self.capabilities, attr, "unknown")
            except Exception:
                setattr(self.capabilities, attr, "unsupported")
        # Memory, watchpoint limits, RTOS awareness, flash programming, and fault
        # diagnostics require safe target-specific evidence and remain unknown.

    def registers_read(self) -> list[dict[str, str]]:
        result = self.mi.command("-data-list-register-values x")
        values = (result.results or {}).get("register-values", [])
        return [
            {
                "name": self.registers[int(item["number"])]
                if int(item["number"]) < len(self.registers)
                else item["number"],
                "value": item.get("value", ""),
            }
            for item in values
        ]

    def variable_read(self, expression: str, *, allow_arbitrary: bool = False) -> Any:
        if not allow_arbitrary and not is_simple_variable_path(expression):
            raise ValueError(
                "variable read accepts only a simple identifier path such as "
                "motor_speed or controller.pid.kp; arbitrary GDB expressions "
                "require --authorize-expression"
            )
        result = self.mi.command(f"-data-evaluate-expression {mi_quote(expression)}")
        return (result.results or {}).get("value")

    def backtrace(self) -> Any:
        return (self.mi.command("-stack-list-frames").results or {}).get("stack", [])

    def thread_select(self, thread_id: str) -> Any:
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", thread_id):
            raise ValueError("GDB thread id must be a numeric or dotted numeric id")
        return self.mi.command(f"-thread-select {thread_id}").results

    def breakpoint_add(self, location: str) -> Any:
        return self.mi.command(f"-break-insert {mi_quote(location)}").results

    def breakpoint_list(self) -> Any:
        return (self.mi.command("-break-list").results or {}).get("BreakpointTable", {})

    def resume(self) -> None:
        self.mi.command("-exec-continue")

    def halt(self) -> None:
        self.mi.command("-exec-interrupt --all")


def mi_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def is_simple_variable_path(expression: str) -> bool:
    """Return whether text is a side-effect-free identifier/member path."""
    return GDBController._VARIABLE_PATH.fullmatch(expression) is not None
