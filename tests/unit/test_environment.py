from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_debug.build_info import build_info
from embedded_debug.environment import (
    inspect_environment,
    install_user_cli,
    remediation_plan,
    repair_user_path,
    rollback_user_path,
)


def _create_fake_cli_venv(destination: Path) -> None:
    scripts = destination / ("Scripts" if os.name == "nt" else "bin")
    python = "python.exe" if os.name == "nt" else "python"
    edbg = "edbg.exe" if os.name == "nt" else "edbg"
    scripts.mkdir(parents=True)
    (scripts / python).touch()
    (scripts / edbg).touch()


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


@pytest.mark.parametrize(
    ("shell", "rc_relative", "original"),
    [
        ("bash", ".bashrc", "# user-owned setting\n\n# preserve this content\n"),
        ("zsh", ".zshrc", "# user-owned setting\n\n# preserve this content"),
        ("fish", ".config/fish/config.fish", "# user-owned setting\n"),
    ],
)
def test_authorized_path_repair_is_idempotent_and_reversible(
    tmp_path, monkeypatch, shell, rc_relative, original
) -> None:
    home = tmp_path / "home"
    tool_dir = tmp_path / "tool chain"
    tool_dir.mkdir(parents=True)
    rc_file = home / rc_relative
    rc_file.parent.mkdir(parents=True)
    rc_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)

    with pytest.raises(PermissionError, match="authorization"):
        repair_user_path(tool_dir, shell=shell)
    assert rc_file.read_text(encoding="utf-8") == original

    first = repair_user_path(tool_dir, shell=shell, authorized=True)
    content = rc_file.read_text(encoding="utf-8")
    assert first["changed"] is True
    if shell == "fish":
        assert f"set -gx PATH '{tool_dir}' $PATH" in content
    else:
        assert f"export PATH='{tool_dir}':\"$PATH\"" in content
    assert "# user-owned setting" in content
    assert repair_user_path(tool_dir, shell=shell, authorized=True)["changed"] is False

    with pytest.raises(PermissionError, match="authorization"):
        rollback_user_path(shell=shell)
    result = rollback_user_path(shell=shell, authorized=True)
    assert result["changed"] is True
    assert rc_file.read_text(encoding="utf-8") == original
    assert rollback_user_path(shell=shell, authorized=True)["changed"] is False


def test_path_repair_rollback_removes_shell_file_it_created(tmp_path, monkeypatch):
    home = tmp_path / "home"
    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    result = repair_user_path(tool_dir, shell="fish", authorized=True)
    rc_file = Path(result["file"])
    assert rc_file.is_file()
    rollback_user_path(shell="fish", authorized=True)
    assert not rc_file.exists()


def test_path_repair_rejects_relative_missing_and_symlinked_shell_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    with pytest.raises(ValueError, match="absolute"):
        repair_user_path("relative/bin", shell="bash", authorized=True)
    with pytest.raises(ValueError, match="does not exist"):
        repair_user_path(tmp_path / "missing", shell="bash", authorized=True)

    target = tmp_path / "real-bashrc"
    target.write_text("# keep\n", encoding="utf-8")
    (home / ".bashrc").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        repair_user_path(tool_dir, shell="bash", authorized=True)
    assert target.read_text(encoding="utf-8") == "# keep\n"


def test_path_rollback_refuses_malformed_markers(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text(
        "# >>> embedded-debug-skill PATH >>>\n", encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    with pytest.raises(ValueError, match="Malformed"):
        rollback_user_path(shell="bash", authorized=True)


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
            _create_fake_cli_venv(destination)

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
            _create_fake_cli_venv(destination)

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
            _create_fake_cli_venv(destination)

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
            _create_fake_cli_venv(destination)

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
