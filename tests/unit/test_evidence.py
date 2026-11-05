from __future__ import annotations

import json

from embedded_debug.evidence import append_evidence
from embedded_debug.models import Envelope


def test_evidence_writer_is_opt_in_and_uses_contract_fields(monkeypatch, tmp_path) -> None:
    path = tmp_path / "nested" / "trace.jsonl"
    monkeypatch.setenv("EDBG_EVIDENCE_FILE", str(path))
    monkeypatch.setenv("EDBG_TASK_ID", "task-1")
    monkeypatch.setenv("EDBG_STEP_ID", "step-2")
    monkeypatch.setenv("EDBG_AUTHORIZATION_SOURCE", "user-confirmed")

    append_evidence(
        Envelope(
            "continue",
            True,
            target={"connection": "connected"},
            target_state="running",
            target_state_before="halted",
            data={"state": "running"},
        )
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["step"] == "step-2"
    assert record["task_id"] == "task-1"
    assert record["authorization_source"] == "user-confirmed"
    assert "session" in record
    assert record["source"] == "cli-json"
    assert record["authorization"] == "run"
    assert record["target_state_before"] == "halted"
    assert record["target_state_after"] == "running"
    assert record["observations"] == {"state": "running"}
    assert record["provenance"] == {}


def test_evidence_writer_does_not_infer_expression_authorization(monkeypatch, tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("EDBG_EVIDENCE_FILE", str(path))

    append_evidence(Envelope("variable-read", True, data={"value": "1"}))

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["authorization"] == "unknown"


def test_evidence_writer_persists_raw_cli_artifact_when_requested(monkeypatch, tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setenv("EDBG_EVIDENCE_FILE", str(path))
    monkeypatch.setenv("EDBG_EVIDENCE_ARTIFACT_DIR", str(artifact_dir))

    envelope = Envelope("status", True, target_state="halted", data={"state": "halted"})
    append_evidence(envelope)

    record = json.loads(path.read_text(encoding="utf-8"))
    artifact = artifact_dir / record["raw_artifact"]
    assert record["raw_artifact"]
    assert json.loads(artifact.read_text(encoding="utf-8"))["command"] == "status"


def test_evidence_writer_preserves_provenance(monkeypatch, tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("EDBG_EVIDENCE_FILE", str(path))
    provenance = {
        "session_id": "session-1",
        "elf_sha256": "a" * 64,
        "gdb_version": None,
    }

    append_evidence(Envelope("status", True, provenance=provenance))

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["provenance"] == provenance


def test_evidence_write_failure_does_not_change_operation(monkeypatch, capsys, tmp_path) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("EDBG_EVIDENCE_FILE", str(parent_file / "trace.jsonl"))

    append_evidence(Envelope("status", True, target_state="halted"))

    assert "Evidence recording failed" in capsys.readouterr().err
