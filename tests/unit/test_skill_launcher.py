from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_launcher():
    path = Path(__file__).parents[2] / "skills/embedded-debug/scripts/run_edbg.py"
    spec = importlib.util.spec_from_file_location("embedded_debug_skill_launcher", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_uses_windows_local_appdata_and_venv_scripts(monkeypatch, tmp_path) -> None:
    launcher = load_launcher()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local App Data"))
    monkeypatch.delenv("EDBG_CACHE_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    cli = launcher._cached_cli()
    assert cli == (
        tmp_path
        / "Local App Data"
        / "embedded-debug-skill"
        / launcher.PINNED_VERSION
        / "Scripts/edbg.exe"
    )
    assert launcher._venv_python(cli).name == "python.exe"
    assert launcher._venv_python(cli).parent.name == "Scripts"


def test_launcher_uses_macos_cache_location(monkeypatch, tmp_path) -> None:
    launcher = load_launcher()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.delenv("EDBG_CACHE_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    cli = launcher._cached_cli()
    assert cli.parents[3] == tmp_path / "Library/Caches"
    assert cli.name == "edbg"


def test_launcher_respects_explicit_cache_override(monkeypatch, tmp_path) -> None:
    launcher = load_launcher()
    monkeypatch.setenv("EDBG_CACHE_HOME", str(tmp_path / "override"))
    assert str(launcher._cached_cli()).startswith(str(tmp_path / "override"))
