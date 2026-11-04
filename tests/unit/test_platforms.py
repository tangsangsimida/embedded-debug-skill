from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from embedded_debug.platforms import get_platform
from embedded_debug.platforms.windows import WindowsPlatform
from embedded_debug.session_store import validate_session_name


def test_platformdirs_honor_edbg_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EDBG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("EDBG_CACHE_HOME", str(tmp_path / "cache"))
    platform = get_platform()
    assert platform.state_dir() == tmp_path / "state" / "edbg"
    assert platform.cache_dir() == tmp_path / "cache" / "edbg"


def test_process_identity_uses_creation_time_and_executable() -> None:
    platform = get_platform()
    identity = platform.process_identity(os.getpid())
    assert identity is not None
    assert identity.created
    assert identity.executable
    assert platform.identity_matches(
        identity,
        pid=os.getpid(),
        created=identity.created,
        executable=identity.executable,
        username=identity.username,
    )
    assert not platform.identity_matches(
        identity,
        pid=os.getpid(),
        created="different-process-start",
        executable=identity.executable,
    )


def test_cross_platform_file_lock_rejects_a_second_nonblocking_owner(tmp_path: Path) -> None:
    platform = get_platform()
    path = tmp_path / "state" / "manager.lock"
    with platform.file_lock(path):
        with pytest.raises(BlockingIOError):
            with platform.file_lock(path, non_blocking=True):
                pytest.fail("second owner unexpectedly acquired the lock")


def test_process_tree_cleanup_only_uses_the_supplied_owned_pid() -> None:
    platform = get_platform()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert platform.terminate_process_tree(child.pid, timeout=0.5)
        assert child.wait(timeout=2) is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_process_tree_cleanup_refuses_a_mismatched_process_identity() -> None:
    platform = get_platform()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = platform.process_identity(child.pid)
        assert identity is not None
        assert not platform.terminate_process_tree(
            child.pid,
            timeout=0.2,
            expected_identity=replace(identity, created="different-process-start"),
        )
        assert child.poll() is None
    finally:
        if child.poll() is None:
            assert platform.terminate_process_tree(
                child.pid, timeout=0.5, expected_identity=platform.process_identity(child.pid)
            )
            child.wait(timeout=2)


def test_python_script_arguments_preserve_unicode_and_spaces(tmp_path: Path) -> None:
    script = tmp_path / "目录 with spaces" / "fake tool.py"
    script.parent.mkdir()
    output = tmp_path / "output file.txt"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')\n",
        encoding="utf-8",
    )
    argv = get_platform().executable_argv(str(script), str(output), "路径 with spaces")
    result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == "路径 with spaces"


def test_ipc_server_binds_loopback_and_rejects_remote_endpoint() -> None:
    platform = get_platform()
    server, endpoint = platform.create_ipc_server()
    try:
        assert endpoint["kind"] == "tcp"
        assert endpoint["host"] == "127.0.0.1"
        assert isinstance(endpoint["port"], int)
        with pytest.raises(ValueError, match="local loopback"):
            platform.connect_ipc({"kind": "tcp", "host": "0.0.0.0", "port": endpoint["port"]})
    finally:
        server.close()


def test_windows_adapter_supplies_safe_process_group_options() -> None:
    options = WindowsPlatform().creation_options()
    assert isinstance(options.get("creationflags"), int)
    assert options["creationflags"] != 0


def test_session_name_rejects_path_separators_and_empty_names() -> None:
    assert validate_session_name("bench-board_2") == "bench-board_2"
    for name in ("", "../other", "a/b", "a\\b", "-leading"):
        with pytest.raises(ValueError):
            validate_session_name(name)
