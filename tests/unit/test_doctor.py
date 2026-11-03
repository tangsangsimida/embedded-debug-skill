from embedded_debug.doctor import inspect_tool


def test_missing_tool_is_diagnostic(monkeypatch) -> None:
    monkeypatch.setattr("embedded_debug.doctor.shutil.which", lambda _: None)
    result = inspect_tool("GDB", "missing-gdb", required=False)
    assert result["status"] == "missing"
    assert not result["required"]


def test_broken_tool_is_invalid(monkeypatch, tmp_path) -> None:
    fake = tmp_path / "fake"
    fake.write_text("#!/bin/sh\nexit 2\n")
    fake.chmod(0o755)
    monkeypatch.setattr("embedded_debug.doctor.shutil.which", lambda _: str(fake))
    result = inspect_tool("tool", str(fake), required=True)
    assert result["status"] == "invalid"
