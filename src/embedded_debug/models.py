"""Shared result and capability models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Capability = Literal["supported", "unsupported", "unknown", "unconfigured"]


@dataclass(slots=True)
class CapabilitySet:
    register_read: Capability = "unknown"
    memory_read: Capability = "unknown"
    breakpoints: Capability = "unknown"
    watchpoints: Capability = "unknown"
    flash_program: Capability = "unknown"
    multicore: Capability = "unknown"
    rtos_awareness: Capability = "unknown"
    fault_diagnostics: Capability = "unknown"
    svd: Capability = "unconfigured"
    telemetry: Capability = "unconfigured"


@dataclass(slots=True)
class Envelope:
    command: str
    success: bool
    target: dict[str, Any] = field(default_factory=dict)
    target_state: str = "unknown"
    target_state_before: str = "unknown"
    data: Any = None
    error: dict[str, str] | None = None
    diagnostics: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
