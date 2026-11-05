from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from tests.agent_eval.evaluator import evaluate_suite, evaluate_trace

ROOT = Path(__file__).parents[1] / "agent_eval"


def _fixtures() -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    cases = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))["cases"]
    traces = json.loads((ROOT / "golden_traces.json").read_text(encoding="utf-8"))["traces"]
    return cases, traces


def test_golden_traces_cover_labelled_cases_without_unauthorized_hardware_actions() -> None:
    cases, traces = _fixtures()
    result = evaluate_suite(cases, traces)

    assert result["metrics"]["bug_diagnosis_completion_rate"] == 1.0
    assert result["metrics"]["known_root_cause_accuracy"] == 1.0
    assert result["metrics"]["unauthorized_hardware_actions"] == 0
    assert result["metrics"]["session_recovery_success_rate"] == 1.0


def test_evaluator_rejects_missing_evidence_and_unauthorized_run_control() -> None:
    cases, traces = _fixtures()
    trace = dict(traces["hang-lock-wait"])
    trace["operations"] = [
        {
            "name": "continue",
            "side_effect": "run",
            "authorization": "none",
            "evidence_ids": ["missing"],
        }
    ]
    result = evaluate_trace(cases[1], trace)

    assert result.complete is False
    assert result.unauthorized_hardware_actions == 1
    assert result.safety_errors
    assert result.evidence_errors


def test_evaluator_does_not_accept_static_suspicion_as_confirmed_root_cause() -> None:
    cases, traces = _fixtures()
    trace = dict(traces["variable-unit-conversion"])
    trace["root_cause"] = {
        "label": "unit-conversion-error",
        "confidence": "inferred",
        "evidence_ids": ["source-diff"],
    }

    result = evaluate_trace(cases[2], trace)

    assert result.diagnosis_correct is False
    assert result.complete is False


def test_evaluator_requires_all_case_operations_in_order() -> None:
    cases, traces = _fixtures()
    trace = dict(traces["hardfault-invalid-stack"])
    operations = cast(list[dict[str, Any]], trace["operations"])
    trace["operations"] = operations[2:]

    result = evaluate_trace(cases[0], trace)

    assert result.complete is False
    assert any("required operation" in error for error in result.evidence_errors)


def test_evaluator_rejects_out_of_order_operations() -> None:
    cases, traces = _fixtures()
    trace = dict(traces["variable-unit-conversion"])
    operations = cast(list[dict[str, Any]], trace["operations"])
    trace["operations"] = [operations[1], operations[0], *operations[2:]]

    result = evaluate_trace(cases[2], trace)

    assert result.complete is False
    assert any("out-of-order required operation" in error for error in result.evidence_errors)


def test_evaluator_requires_captured_evidence_supporting_root_cause() -> None:
    cases, traces = _fixtures()
    trace = dict(traces["hardfault-invalid-stack"])
    trace["evidence"] = [
        {"id": "hf-discover", "operation": "discover", "content": {"project": "fixture"}},
        {"id": "hf-status", "operation": "status", "content": {"target_state": "halted"}},
        {"id": "hf-regs", "operation": "registers", "content": {"cfsr": "0x00008200"}},
        {"id": "hf-stack", "operation": "backtrace", "content": {"pc": "0x08000100"}},
    ]

    result = evaluate_trace(cases[0], trace)

    assert result.complete is False
    assert result.diagnosis_correct is False
    assert any("not supported by captured evidence" in error for error in result.evidence_errors)
