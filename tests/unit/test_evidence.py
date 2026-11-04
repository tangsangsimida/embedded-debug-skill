from __future__ import annotations

import json

from embedded_debug.evidence import append_evidence
from embedded_debug.models import Envelope


def test_evidence_writer_is_opt_in_and_uses_contract_fields(monkeypatch, tmp_path) -> None:
    path = tmp_path / "nested" / "trace.jsonl"
    monkeypatch.setenv("EDBG_EVIDENCE_FILE", str(path))

    append_evidence(
        Envelope(
            "continue",
            True,
            target={"connection": "connected"},
            target_state="running",
            data={"state": "running"},
        )
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["step"] == "continue"
    assert record["source"] == "cli-json"
    assert record["authorization"] == "run"
    assert record["target_state_before"] == "unknown"
    assert record["target_state_after"] == "running"
    assert record["observations"] == {"state": "running"}


def test_evidence_writer_does_not_infer_expression_authorization(monkeypatch, tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("EDBG_EVIDENCE_FILE", str(path))

    append_evidence(Envelope("variable-read", True, data={"value": "1"}))

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["authorization"] == "unknown"


def test_evidence_write_failure_does_not_change_operation(monkeypatch, capsys, tmp_path) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("EDBG_EVIDENCE_FILE", str(parent_file / "trace.jsonl"))

    append_evidence(Envelope("status", True, target_state="halted"))

    assert "Evidence recording failed" in capsys.readouterr().err
