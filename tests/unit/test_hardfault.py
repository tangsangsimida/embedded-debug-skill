from __future__ import annotations

import json

from embedded_debug.diagnostics.hardfault import analyze_snapshot, analyze_snapshot_file


def _snapshot() -> dict[str, object]:
    return {
        "architecture": "armv7e-m",
        "registers": [
            {"name": "lr", "value": "0xfffffff9"},
            {"name": "pc", "value": "0x08000100"},
            {"name": "cfsr", "value": "0x00008200"},
            {"name": "hfsr", "value": "0x40000000"},
        ],
        "stacked_frame": {
            "r0": "0", "r1": "1", "r2": "2", "r3": "3",
            "r12": "12", "lr": "0x08000021", "pc": "0x08001234", "xpsr": "0x21000000",
        },
    }


def test_hardfault_decoder_uses_stacked_pc_and_decodes_cortex_m_flags() -> None:
    result = analyze_snapshot(_snapshot())

    assert result["status"] == "analyzed"
    assert result["confidence"] == "confirmed"
    assert result["fault_pc"] == "0x8001234"
    assert result["live_pc"] == "0x8000100"
    assert result["stack_source"] == "msp"
    assert "PRECISERR" in result["fault_status"]["flags"]["bfsr"]
    assert "FORCED" in result["fault_status"]["flags"]["hfsr"]


def test_hardfault_decoder_preserves_unknown_architecture_and_missing_frame() -> None:
    result = analyze_snapshot({"architecture": "riscv:rv32", "registers": {"pc": "0x10"}})

    assert result["status"] == "unknown"
    assert result["confidence"] == "unknown"
    assert result["fault_pc"] is None
    assert result["limitations"]


def test_hardfault_decoder_uses_gdb_frame_architecture_when_top_level_is_auto() -> None:
    snapshot = _snapshot()
    snapshot["architecture"] = "auto"
    snapshot["backtrace"] = [{"frame": {"arch": "armv7e-m"}}]

    result = analyze_snapshot(snapshot)

    assert result["status"] == "analyzed"
    assert result["architecture"] == "armv7e-m"
    assert result["architecture_source"] == "backtrace"


def test_hardfault_cli_snapshot_envelope_is_supported(tmp_path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"data": _snapshot()}), encoding="utf-8")

    result = analyze_snapshot_file(path)

    assert result["fault_pc"] == "0x8001234"
