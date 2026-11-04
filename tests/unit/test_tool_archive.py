import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from embedded_debug.cli import run
from embedded_debug.tool_archive import install_tool_archive, remove_tool_archive


def archive(tmp_path, name, content):
    path = tmp_path / "tool.zip"
    with zipfile.ZipFile(path, "w") as package:
        entry = zipfile.ZipInfo(name)
        # ZipInfo normally rewrites the host separator on Windows. Preserve the
        # exact untrusted archive entry so this test exercises the same bytes.
        entry.filename = name
        package.writestr(entry, content)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_authorized_isolated_install_verify_and_rollback(tmp_path, monkeypatch):
    monkeypatch.setenv("EDBG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("EDBG_STATE_HOME", str(tmp_path / "state"))
    script = """import sys
if '--version' in sys.argv:
    print('test GDB 1.0')
else:
    print('1^done')
"""
    path, digest = archive(tmp_path, "bin/gdb.py", script)
    with pytest.raises(PermissionError):
        install_tool_archive(str(path), digest, "bin/gdb.py", "gdb")
    result = install_tool_archive(str(path), digest, "bin/gdb.py", "gdb", authorized=True)
    assert result["version"] == "test GDB 1.0"
    assert result["mi2"] == "supported"
    assert result["path"].startswith(str(tmp_path / "cache"))
    with pytest.raises(PermissionError):
        remove_tool_archive(digest)
    monkeypatch.setattr("embedded_debug.session_store.session_names", lambda: ["board-a"])
    monkeypatch.setattr(
        "embedded_debug.session_store.read_session", lambda name: {"gdb_executable": result["path"]}
    )
    with pytest.raises(RuntimeError, match="board-a"):
        remove_tool_archive(digest, authorized=True)
    monkeypatch.setattr("embedded_debug.session_store.session_names", lambda: [])
    remove_tool_archive(digest, authorized=True)
    assert not Path(result["path"]).exists()


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "dir/CON", "dir/a\\b", "dir\\..\\escape"]
)
def test_archive_digest_and_traversal_fail_without_install(tmp_path, monkeypatch, name):
    monkeypatch.setenv("EDBG_CACHE_HOME", str(tmp_path / "cache"))
    path, digest = archive(tmp_path, name, "bad")
    with pytest.raises(ValueError, match="SHA-256"):
        install_tool_archive(str(path), "0" * 64, "bin/gdb", "gdb", authorized=True)
    with pytest.raises(ValueError, match="Unsafe archive path"):
        install_tool_archive(str(path), digest, "bin/gdb", "gdb", authorized=True)
    assert not (tmp_path / "escape").exists()


def test_archive_link_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("EDBG_CACHE_HOME", str(tmp_path / "cache"))
    path = tmp_path / "link.zip"
    entry = zipfile.ZipInfo("bin/link")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(entry, "../../outside")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="link or special"):
        install_tool_archive(str(path), digest, "bin/link", "gdb", authorized=True)


def test_failed_tool_validation_leaves_no_managed_install(tmp_path, monkeypatch):
    monkeypatch.setenv("EDBG_CACHE_HOME", str(tmp_path / "cache"))
    path, digest = archive(tmp_path, "bin/gdb.py", "raise SystemExit(1)")
    with pytest.raises(RuntimeError, match="failed version check"):
        install_tool_archive(str(path), digest, "bin/gdb.py", "gdb", authorized=True)
    assert not (tmp_path / "cache" / "edbg" / "tool-archives" / digest).exists()


def test_cli_requires_explicit_archive_authorization(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EDBG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("EDBG_STATE_HOME", str(tmp_path / "state"))
    path, digest = archive(tmp_path, "bin/openocd.py", "print('OpenOCD test')")
    args = [
        "--json",
        "environment",
        "install-tool-archive",
        "--source",
        str(path),
        "--sha256",
        digest,
        "--executable",
        "bin/openocd.py",
        "--kind",
        "openocd",
    ]
    assert run(args) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "AUTHORIZATION_REQUIRED"
    assert run([*args, "--authorize"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["data"]["version"] == "OpenOCD test"
    assert Path(response["data"]["path"]).is_file()
