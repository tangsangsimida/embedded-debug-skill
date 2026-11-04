from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
SKILL = ROOT / "skills" / "embedded-debug"


def test_autonomous_workflow_has_required_agent_phases_and_safety_gates() -> None:
    workflow = (SKILL / "references" / "workflow.md").read_text(encoding="utf-8")
    for phase in (
        "Understand",
        "Discover",
        "Plan",
        "Execute",
        "Reason and verify",
        "Fix and regress",
        "Report and clean up",
    ):
        assert phase in workflow
    normalized = " ".join(workflow.split()).lower()
    for invariant in (
        "target_state=halted",
        "explicit authorization",
        "unknown",
        "hypothesis table",
        "re-plan after each result",
        "retry the same failing operation at most once",
    ):
        assert invariant in normalized


def test_evidence_contract_requires_claim_levels_and_hardware_identity() -> None:
    evidence = (SKILL / "references" / "evidence.md").read_text(encoding="utf-8")
    normalized = " ".join(evidence.split()).lower()
    for required in (
        '"target_state_before"',
        '"authorization"',
        "observed",
        "confirmed",
        "inferred",
        "unknown",
        "rejected",
        "exact repository commit",
        "ELF identity",
        "session cleanup result",
    ):
        assert required.lower() in normalized


def test_diagnostic_routes_are_present_and_linked_from_skill() -> None:
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    diagnostics = SKILL / "references" / "diagnostics"
    expected = {
        "hang.md",
        "rtos.md",
        "memory.md",
        "interrupt.md",
        "dma.md",
        "communication.md",
        "peripheral.md",
        "startup.md",
    }
    assert expected == {path.name for path in diagnostics.iterdir() if path.is_file()}
    assert "references/diagnostics/" in skill
