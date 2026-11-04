"""Offline Cortex-M fault-frame analysis from an explicit JSON snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SUPPORTED_CORTEX_M = {"armv7-m", "armv7e-m", "armv8-m.main", "armv8-m.base"}
_MMFSR = {
    0: "IACCVIOL", 1: "DACCVIOL", 3: "MUNSTKERR", 4: "MSTKERR", 5: "MLSPERR", 7: "MMARVALID"
}
_BFSR = {
    0: "IBUSERR", 1: "PRECISERR", 2: "IMPRECISERR", 3: "UNSTKERR",
    4: "STKERR", 5: "LSPERR", 7: "BFARVALID"
}
_UFSR = {
    0: "UNDEFINSTR", 1: "INVSTATE", 2: "INVPC", 3: "NOCP", 8: "UNALIGNED", 9: "DIVBYZERO"
}
_HFSR = {1: "VECTTBL", 30: "FORCED", 31: "DEBUGEVT"}
_FRAME_FIELDS = ("r0", "r1", "r2", "r3", "r12", "lr", "pc", "xpsr")


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError:
            try:
                return int(value.strip(), 16)
            except ValueError:
                return None
    return None


def _hex(value: int | None) -> str | None:
    return None if value is None else f"0x{value:x}"


def _registers(snapshot: dict[str, Any]) -> dict[str, int]:
    source = snapshot.get("registers", {})
    if isinstance(source, dict):
        pairs = list(source.items())
    elif isinstance(source, list):
        pairs = [
            (item.get("name"), item.get("value"))
            for item in source
            if isinstance(item, dict)
        ]
    else:
        pairs = []
    result: dict[str, int] = {}
    for name, value in pairs:
        parsed = _integer(value)
        if isinstance(name, str) and parsed is not None:
            result[name.lower()] = parsed
    return result


def _flags(value: int | None, names: dict[int, str], offset: int = 0) -> list[str]:
    if value is None:
        return []
    return [name for bit, name in names.items() if value & (1 << (bit + offset))]


def _stacked_frame(snapshot: dict[str, Any]) -> dict[str, str] | None:
    frame = snapshot.get("stacked_frame")
    if not isinstance(frame, dict):
        return None
    values = {field: _integer(frame.get(field)) for field in _FRAME_FIELDS}
    if any(value is None for value in values.values()):
        return None
    return {field: _hex(value) or "0x0" for field, value in values.items()}


def analyze_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Analyze only evidence explicitly present in ``snapshot``."""

    architecture = str(snapshot.get("architecture", "unknown")).strip().lower()
    supported = architecture in SUPPORTED_CORTEX_M
    registers = _registers(snapshot)
    limitations: list[str] = []
    if not supported:
        limitations.append("Cortex-M fault decoding requires a confirmed GDB architecture")
    cfsr = registers.get("cfsr")
    hfsr = registers.get("hfsr")
    frame = _stacked_frame(snapshot)
    exc_return = registers.get("lr")
    stack_source = None
    if exc_return is not None and (exc_return & 0xFFFFFF00) == 0xFFFFFF00:
        stack_source = "psp" if exc_return & 0x4 else "msp"
    elif exc_return is not None:
        limitations.append("LR is not a recognized EXC_RETURN value")
    else:
        limitations.append("EXC_RETURN/LR is missing")
    if frame is None:
        limitations.append("complete stacked exception frame is missing or invalid")
    flags = {
        "mmfsr": _flags(cfsr, _MMFSR),
        "bfsr": _flags(cfsr, _BFSR, 8),
        "ufsr": _flags(cfsr, _UFSR, 16),
        "hfsr": _flags(hfsr, _HFSR),
    }
    any_fault_flag = any(flags.values())
    fault_pc = _integer(frame.get("pc")) if frame else None
    if supported and frame and any_fault_flag:
        confidence = "confirmed"
    elif supported and (frame or any_fault_flag):
        confidence = "inferred"
    else:
        confidence = "unknown"
    if not any_fault_flag:
        limitations.append("CFSR/HFSR has no decoded fault flag")
    return {
        "status": "analyzed" if supported else "unknown",
        "architecture": architecture,
        "architecture_supported": supported,
        "confidence": confidence,
        "fault_pc": _hex(fault_pc),
        "live_pc": _hex(registers.get("pc")),
        "stack_source": stack_source,
        "exc_return": _hex(exc_return),
        "fault_status": {"cfsr": _hex(cfsr), "hfsr": _hex(hfsr), "flags": flags},
        "stacked_frame": frame,
        "limitations": limitations,
    }


def analyze_snapshot_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read fault snapshot {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("Fault snapshot must be a JSON object")
    data = loaded.get("data", loaded)
    if not isinstance(data, dict):
        raise ValueError("Fault snapshot data must be a JSON object")
    return analyze_snapshot(data)
