from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "hardware" / "hil_runner.py"
SPEC = importlib.util.spec_from_file_location("hil_runner", MODULE_PATH)
assert SPEC and SPEC.loader
HIL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HIL)


def test_hil_requires_existing_elf_for_variable_checks(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="require --elf"):
        HIL.run_hil({"expressions": ["counter"], "connect_args": []})


def test_hil_run_control_requires_two_explicit_opt_ins(monkeypatch) -> None:
    monkeypatch.delenv("EDBG_HARDWARE_RUN_CONTROL", raising=False)
    with pytest.raises(PermissionError, match="EDBG_HARDWARE_RUN_CONTROL"):
        HIL.run_hil({"run_control": True, "authorize_run_control": True})


def test_hil_breakpoints_require_config_authorization() -> None:
    with pytest.raises(PermissionError, match="authorize_breakpoints"):
        HIL.run_hil({"breakpoints": ["main"]})


def test_hil_config_validates_shape(tmp_path: Path) -> None:
    config = tmp_path / "hardware.json"
    config.write_text('{"connect_args": "not-an-array"}', encoding="utf-8")
    with pytest.raises(ValueError, match="list of strings"):
        HIL.load_config(config)


def test_mock_hil_exercises_run_control_breakpoints_and_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    log = tmp_path / "calls.jsonl"
    state = tmp_path / "state"
    mock_cli = tmp_path / "mock_cli.py"
    mock_cli.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "at = args.index('--session')\n"
        "command = args[at + 2:]\n"
        "name = command[0]\n"
        "Path(os.environ['MOCK_CALL_LOG']).open('a').write(json.dumps(command) + '\\n')\n"
        "state = Path(os.environ['MOCK_STATE'])\n"
        "if name == 'continue': state.write_text('running')\n"
        "if name == 'halt': state.write_text('halted')\n"
        "target_state = state.read_text() if state.exists() else 'halted'\n"
        "data = []\n"
        "target = {}\n"
        "if name == 'connect': target = {'connection': 'connected'}\n"
        "if name == 'breakpoint': data = {'number': '1'}\n"
        "if name == 'session' and command[1] == 'stop': data = {'stopped': True}\n"
        "print(json.dumps({'success': True, 'command': name, 'target': target, "
        "'target_state': target_state, 'data': data}))\n",
        encoding="utf-8",
    )
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(b"mock elf")
    monkeypatch.setenv("MOCK_CALL_LOG", str(log))
    monkeypatch.setenv("MOCK_STATE", str(state))
    monkeypatch.setenv("EDBG_HARDWARE_RUN_CONTROL", "1")
    config = {
        "cli": sys.executable,
        "global_args": [str(mock_cli)],
        "session": "test-hil",
        "connect_args": ["--elf", str(elf)],
        "breakpoints": ["main"],
        "authorize_breakpoints": True,
        "run_control": True,
        "authorize_run_control": True,
    }

    result = HIL.run_hil(config)

    assert result["registers"]["success"] is True
    assert result["cleanup"]["data"]["stopped"] is True
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[-1] == ["session", "stop"]
    assert ["breakpoint", "remove", "1"] in calls
    assert ["step", "--authorize-run"] in calls
    assert ["continue", "--authorize-run"] in calls


def test_hil_preserves_primary_failure_when_session_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def invoke(_config: dict[str, object], *args: str) -> dict[str, object]:
        calls.append(args)
        if args == ("session", "status"):
            return {"success": True, "data": {"active": False}}
        if args == ("connect",):
            return {"success": True, "target": {"connection": "connected"}}
        if args == ("status",):
            return {"success": True, "target_state": "halted"}
        if args == ("registers",):
            raise AssertionError("register read failed")
        if args == ("session", "stop"):
            raise AssertionError("session stop failed")
        raise AssertionError(f"unexpected HIL command: {args!r}")

    monkeypatch.setattr(HIL, "_invoke", invoke)

    with pytest.raises(AssertionError, match="register read failed") as raised:
        HIL.run_hil({"session": "failure-case"})

    assert any("session stop failed" in note for note in raised.value.__notes__)
    assert calls[-1] == ("session", "stop")
