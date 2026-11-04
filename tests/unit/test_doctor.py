from embedded_debug.doctor import _shell_guidance, inspect_tool


def test_missing_tool_is_diagnostic(monkeypatch) -> None:
    monkeypatch.setattr("embedded_debug.doctor.shutil.which", lambda _: None)
    result = inspect_tool("GDB", "missing-gdb", required=False)
    assert result["status"] == "missing"
    assert not result["required"]


def test_broken_tool_is_invalid(monkeypatch, tmp_path) -> None:
    fake = tmp_path / "fake.py"
    fake.write_text("raise SystemExit(2)\n")
    monkeypatch.setattr("embedded_debug.doctor.shutil.which", lambda _: str(fake))
    result = inspect_tool("tool", str(fake), required=True)
    assert result["status"] == "invalid"


def test_windows_shell_guidance_is_read_only(monkeypatch) -> None:
    monkeypatch.setattr("embedded_debug.doctor.sys.platform", "win32")
    monkeypatch.setenv("PSModulePath", "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\Modules")
    assert "$env:Path" in _shell_guidance()
