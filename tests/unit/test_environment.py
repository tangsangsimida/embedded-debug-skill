from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_debug.build_info import build_info
from embedded_debug.environment import inspect_environment, install_user_cli, remediation_plan


def test_inspection_uses_overrides_and_keeps_optional_tools_optional(monkeypatch) -> None:
    seen: list[tuple[str, str]] = []

    def fake_inspect(name: str, executable: str, *, required: bool) -> dict[str, object]:
        seen.append((name, executable))
        return {
            "name": name,
            "executable": executable,
            "required": required,
            "status": "available" if name == "Python" else "missing",
        }

    monkeypatch.setattr("embedded_debug.environment.inspect_tool", fake_inspect)
    monkeypatch.setattr("embedded_debug.environment.inspect_gdb_mi", lambda _: "unknown")
    result = inspect_environment({"gdb": "/custom/gdb"})
    assert ("ARM GDB", "/custom/gdb") in seen
    assert result["cli_ready"] is True
    assert result["debug_tools_ready"] is False
    assert result["tools"]["gdb"]["role"] == "optional"


def test_remediation_plan_is_advisory_and_never_mutates_path(monkeypatch) -> None:
    original = os.environ.get("PATH")
    inspection = {
        "tools": {
            "gdb": {"status": "missing", "executable": "arm-none-eabi-gdb"},
            "openocd": {"status": "available", "executable": "openocd"},
        }
    }
    plan = remediation_plan(inspection, shell="fish")
    assert len(plan) == 1
    assert plan[0]["tool"] == "arm-none-eabi-gdb"
    assert plan[0]["path_guidance"].startswith("fish_add_path ")
    assert os.environ.get("PATH") == original


def test_user_cli_install_requires_explicit_authorization(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("embedded_debug.environment.user_cache_path", lambda *_: tmp_path)
    with pytest.raises(PermissionError, match="explicit authorization"):
        install_user_cli("embedded-debug-skill==0.1.0")
    assert list(tmp_path.iterdir()) == []


def test_user_cli_install_requires_an_explicit_source() -> None:
    with pytest.raises(PermissionError):
        install_user_cli("")
    with pytest.raises(ValueError, match="source"):
        install_user_cli(" ", authorized=True)


def test_authorized_cli_install_is_versioned_and_reuses_completed_install(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("embedded_debug.environment.user_cache_path", lambda *_: tmp_path)

    class FakeBuilder:
        def __init__(self, **_kwargs):
            pass

        def create(self, destination):
            scripts = destination / "bin"
            scripts.mkdir(parents=True)
            (scripts / "python").touch()
            (scripts / "edbg").touch()

    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        stdout = (
            "installed\n"
            if "-m" in command
            else json.dumps({"success": True, "data": build_info()})
        )
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("embedded_debug.environment.venv.EnvBuilder", FakeBuilder)
    monkeypatch.setattr("embedded_debug.environment.subprocess.run", fake_run)
    installed = install_user_cli("embedded-debug-skill==0.1.0", authorized=True)
    assert installed["version"] == "0.1.0"
    assert Path(installed["path"]).is_file()
    assert len(calls) == 2
    assert calls[0][-1] == "embedded-debug-skill==0.1.0"
    assert install_user_cli("different-source", authorized=True) == installed
    assert len(calls) == 3


def test_failed_user_cli_install_rolls_back_only_partial_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("embedded_debug.environment.user_cache_path", lambda *_: tmp_path)

    class FakeBuilder:
        def __init__(self, **_kwargs):
            pass

        def create(self, destination):
            (destination / "bin").mkdir(parents=True)
            (destination / "bin" / "python").touch()

    monkeypatch.setattr("embedded_debug.environment.venv.EnvBuilder", FakeBuilder)
    monkeypatch.setattr(
        "embedded_debug.environment.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="install failed"),
    )
    with pytest.raises(RuntimeError, match="install failed"):
        install_user_cli("embedded-debug-skill==0.1.0", authorized=True)
    assert list((tmp_path / "cli").iterdir()) == []


def test_source_mismatch_rolls_back_new_user_cli_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("embedded_debug.environment.user_cache_path", lambda *_: tmp_path)

    class FakeBuilder:
        def __init__(self, **_kwargs):
            pass

        def create(self, destination):
            (destination / "bin").mkdir(parents=True)
            (destination / "bin" / "python").touch()
            (destination / "bin" / "edbg").touch()

    def fake_run(command, **_kwargs):
        if "-m" in command:
            return SimpleNamespace(returncode=0, stdout="installed", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"success": True, "data": {"version": "0.1.0", "source_digest": "wrong"}}
            ),
            stderr="",
        )

    monkeypatch.setattr("embedded_debug.environment.venv.EnvBuilder", FakeBuilder)
    monkeypatch.setattr("embedded_debug.environment.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="version/source"):
        install_user_cli("embedded-debug-skill==0.1.0", authorized=True)
    assert list((tmp_path / "cli").iterdir()) == []


def test_user_cli_install_falls_back_when_ensurepip_is_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("embedded_debug.environment.user_cache_path", lambda *_: tmp_path)

    class FakeBuilder:
        def __init__(self, *, with_pip, **_kwargs):
            self.with_pip = with_pip

        def create(self, destination):
            if self.with_pip:
                raise SystemExit("ensurepip unavailable")
            scripts = destination / "bin"
            scripts.mkdir(parents=True)
            (scripts / "python").touch()
            (scripts / "edbg").touch()

    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        stdout = (
            "installed"
            if "install" in command
            else json.dumps({"success": True, "data": build_info()})
        )
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("embedded_debug.environment.venv.EnvBuilder", FakeBuilder)
    monkeypatch.setattr("embedded_debug.environment.subprocess.run", fake_run)
    result = install_user_cli("embedded-debug-skill==0.1.0", authorized=True)
    assert result["version"] == "0.1.0"
    assert "--python" in calls[0]
