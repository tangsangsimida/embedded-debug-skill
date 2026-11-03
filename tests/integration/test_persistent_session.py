"""Cross-process tests for the Unix-socket GDB session manager."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import psutil
import pytest

from embedded_debug.session_manager import SessionManagerError, start_session
from embedded_debug.session_store import gdb_identity_matches, process_alive, read_session

MOCKS = Path(__file__).parents[1] / "mocks"


def _listener() -> socket.socket:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    return server


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("EDBG_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("EDBG_FAKE_GDB_LOG", str(tmp_path / "gdb.log"))
    result = os.environ.copy()
    result["XDG_STATE_HOME"] = str(tmp_path / "state")
    result["EDBG_AUDIT_LOG"] = str(tmp_path / "audit.jsonl")
    result["EDBG_FAKE_GDB_LOG"] = str(tmp_path / "gdb.log")
    return result


def _invoke(cwd: Path, env: dict[str, str], *args: str) -> tuple[int, dict[str, Any]]:
    result = subprocess.run(
        [sys.executable, "-m", "embedded_debug.cli", "--json", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.stdout, f"CLI produced no JSON (exit {result.returncode}): {result.stderr}"
    return result.returncode, json.loads(result.stdout)


def _start(cwd: Path, env: dict[str, str], port: int, *options: str) -> dict[str, Any]:
    global_options: list[str] = []
    connect_options: list[str] = []
    global_flags = {"--gdb", "--gdb-timeout", "--openocd", "--session"}
    index = 0
    while index < len(options):
        destination = global_options if options[index] in global_flags else connect_options
        destination.extend(options[index : index + 2])
        index += 2
    code, payload = _invoke(
        cwd,
        env,
        *global_options,
        "connect",
        "--host",
        "127.0.0.1",
        "--gdb-port",
        str(port),
        *connect_options,
    )
    assert code == 0, payload
    assert payload["target"]["connection"] == "connected"
    return payload


def _stop(cwd: Path, env: dict[str, str]) -> None:
    _invoke(cwd, env, "session", "stop")


def _wait_for(
    cwd: Path, env: dict[str, str], predicate: Any, timeout: float = 4.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, payload = _invoke(cwd, env, "session", "status")
        if code == 0 and predicate(payload["data"]):
            return payload["data"]
        time.sleep(0.03)
    raise AssertionError("debug session did not reach its expected state")


def test_connect_reuses_vscode_configuration_without_edbg_file_or_target_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    build = tmp_path / "build"
    build.mkdir()
    (build / "firmware.elf").write_bytes(b"mock ELF")
    (vscode / "launch.json").write_text(
        json.dumps(
            {
                "configurations": [
                    {
                        "name": "existing OpenOCD debug",
                        "servertype": "openocd",
                        "gdbTarget": "127.0.0.1:3333",
                        "configFiles": ["interface/cmsis-dap.cfg", "board/user-board.cfg"],
                        "gdbExecutable": str(MOCKS / "fake_gdb.py"),
                        "executable": "${workspaceFolder}/build/firmware.elf",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    server = _listener()
    port = server.getsockname()[1]
    launch = json.loads((vscode / "launch.json").read_text())
    launch["configurations"][0]["gdbTarget"] = f"127.0.0.1:{port}"
    (vscode / "launch.json").write_text(json.dumps(launch), encoding="utf-8")
    try:
        code, connected = _invoke(tmp_path, env, "connect")
        assert code == 0, connected
        assert connected["target"]["connection"] == "connected"
        assert Path(connected["data"]["elf"]).parts[-2:] == ("build", "firmware.elf")
        assert not (tmp_path / ".edbg.toml").exists()
        assert not (tmp_path / "edbg.toml").exists()
        starts = [
            line
            for line in (tmp_path / "gdb.log").read_text().splitlines()
            if line.startswith("START ")
        ]
        assert len(starts) == 1
    finally:
        _stop(tmp_path, env)
        server.close()


def test_flash_reuses_project_task_only_after_explicit_command_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    marker = tmp_path / "flash-invoked"
    task = {
        "version": "2.0.0",
        "tasks": [
            {
                "label": "flash firmware",
                "type": "process",
                "command": sys.executable,
                "args": ["-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            }
        ],
    }
    (vscode / "tasks.json").write_text(json.dumps(task), encoding="utf-8")
    try:
        code, refused = _invoke(tmp_path, env, "flash", "--authorize")
        assert code == 2
        assert "authorize-command" in refused["error"]["message"]
        assert not marker.exists()

        code, flashed = _invoke(tmp_path, env, "flash", "--authorize", "--authorize-command")
        assert code == 0, flashed
        assert marker.exists()
        assert flashed["data"]["source"].endswith("tasks.json")
    finally:
        _stop(tmp_path, env)


def test_startup_timeout_cleans_gdb_child_before_aborting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path)
    delay_file = tmp_path / "delay-target-select"
    delay_file.touch()
    monkeypatch.setenv("EDBG_FAKE_GDB_TARGET_DELAY_FILE", str(delay_file))
    server = _listener()
    settings = {
        "host": "127.0.0.1",
        "gdb_port": server.getsockname()[1],
        "openocd_config": None,
        "interface": None,
        "target_config": None,
        "board": None,
        "config_files": [],
        "server_args": [],
        "commands": [],
        "adapter_khz": None,
        "openocd_executable": "openocd",
        "gdb_executable": str(MOCKS / "fake_gdb.py"),
        "gdb_timeout": 10.0,
        "elf": None,
    }
    try:
        with pytest.raises(SessionManagerError, match="did not become ready"):
            # Leave enough time for slower Windows ARM runners to launch the
            # manager and GDB child before the deliberate target-select stall.
            start_session(settings, timeout=3.0)
        gdb_log = tmp_path / "gdb.log"
        if gdb_log.exists():
            start_records = [
                line for line in gdb_log.read_text().splitlines() if line.startswith("START ")
            ]
            assert len(start_records) == 1
            assert not process_alive(int(start_records[0].split()[1]))
        assert read_session() is None
    finally:
        server.close()


def test_untrusted_workspace_build_task_does_not_run_without_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    marker = tmp_path / "build-invoked"
    task = {
        "version": "2.0.0",
        "tasks": [
            {
                "label": "build firmware",
                "type": "process",
                "command": sys.executable,
                "args": ["-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            }
        ],
    }
    (vscode / "tasks.json").write_text(json.dumps(task), encoding="utf-8")
    try:
        code, refused = _invoke(tmp_path, env, "build")
        assert code == 2
        assert "authorize-command" in refused["error"]["message"]
        assert not marker.exists()

        code, built = _invoke(tmp_path, env, "build", "--authorize-command")
        assert code == 0, built
        assert marker.exists()
        assert built["data"]["exit_code"] == 0
    finally:
        _stop(tmp_path, env)


def test_commands_across_independent_processes_keep_gdb_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    env["EDBG_FAKE_GDB_LINK_FILE"] = str(tmp_path / "link-down")
    monkeypatch.setenv("EDBG_FAKE_GDB_LINK_FILE", env["EDBG_FAKE_GDB_LINK_FILE"])
    server = _listener()
    try:
        started = _start(
            tmp_path,
            env,
            server.getsockname()[1],
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
        )
        assert started["data"]["session"] == "connected"
        record = read_session()
        assert record is not None
        assert record["schema"] == 3
        assert record["ipc_endpoint"]["kind"] == "tcp"
        assert record["ipc_endpoint"]["host"] == "127.0.0.1"
        assert record["ipc_endpoint"]["port"] > 0
        with socket.create_connection(
            (record["ipc_endpoint"]["host"], record["ipc_endpoint"]["port"])
        ) as channel:
            channel.sendall(
                json.dumps(
                    {
                        "session_id": record["session_id"],
                        "token": "invalid-token",
                        "operation": "continue",
                        "params": {"authorized_run": True},
                    }
                ).encode()
                + b"\n"
            )
            with channel.makefile("rb") as response_stream:
                denied = json.loads(response_stream.readline())
        assert not denied["success"]
        assert "IPC authentication failed" in denied["error"]

        code, added = _invoke(tmp_path, env, "breakpoint", "add", "main")
        assert code == 0, added
        assert added["data"]["bkpt"]["number"] == "1"

        code, listed = _invoke(tmp_path, env, "breakpoint", "list")
        assert code == 0, listed
        assert listed["data"]["nr_rows"] == "1"

        code, selected = _invoke(tmp_path, env, "thread", "select", "2")
        assert code == 0, selected
        assert selected["data"]["new-thread-id"] == "2"
        code, threads = _invoke(tmp_path, env, "threads")
        assert code == 0, threads
        assert threads["data"]["gdb_threads"]["current-thread-id"] == "2"

        code, variable = _invoke(tmp_path, env, "variable", "read", "motor_speed")
        assert code == 0, variable
        assert variable["data"]["value"] == "42"

        code, stack = _invoke(tmp_path, env, "backtrace")
        assert code == 0, stack
        assert stack["data"][0]["frame"]["func"] == "main"

        code, registers = _invoke(tmp_path, env, "registers")
        assert code == 0, registers
        assert registers["data"][0] == {"name": "r0", "value": "0x2a"}

        code, resumed = _invoke(tmp_path, env, "continue", "--authorize-run")
        assert code == 0, resumed
        assert resumed["target_state"] in {"running", "halted"}
        state = _wait_for(tmp_path, env, lambda item: item["target_state"] == "halted")
        assert any("*stopped" in event for event in state["async_events"])

        log = (tmp_path / "gdb.log").read_text()
        starts = [line for line in log.splitlines() if line.startswith("START ")]
        assert len(starts) == 1
        assert '-break-insert "main"' in log
        assert "-break-list" in log
        assert "-thread-select 2" in log
        assert '-data-evaluate-expression "motor_speed"' in log
        assert "-stack-list-frames" in log
        assert "-data-list-register-values x" in log
        assert "-exec-continue" in log
    finally:
        _stop(tmp_path, env)
        server.close()
    assert read_session() is None


def test_parallel_cli_requests_are_serialized_and_keep_one_gdb_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    server = _listener()
    try:
        _start(
            tmp_path,
            env,
            server.getsockname()[1],
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
        )
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(_invoke, tmp_path, env, "variable", "read", f"value_{index}")
                for index in range(4)
            ]
        results = [future.result() for future in futures]
        assert all(code == 0 and payload["success"] for code, payload in results)
        starts = [
            line
            for line in (tmp_path / "gdb.log").read_text().splitlines()
            if line.startswith("START ")
        ]
        assert len(starts) == 1
    finally:
        _stop(tmp_path, env)
        server.close()


def test_named_sessions_isolate_multiple_targets_and_refuse_duplicate_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    first, second = _listener(), _listener()
    try:
        _start(
            tmp_path,
            env,
            first.getsockname()[1],
            "--session",
            "board-a",
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
        )
        _start(
            tmp_path,
            env,
            second.getsockname()[1],
            "--session",
            "board-b",
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
        )
        first_record = read_session("board-a")
        second_record = read_session("board-b")
        assert first_record and second_record
        assert first_record["daemon_pid"] != second_record["daemon_pid"]
        assert first_record["gdb_pid"] != second_record["gdb_pid"]

        for session_name in ("board-a", "board-b"):
            code, bp = _invoke(
                tmp_path,
                env,
                "--session",
                session_name,
                "breakpoint",
                "add",
                f"main_{session_name}",
            )
            assert code == 0 and bp["data"]["bkpt"]["number"] == "1"
        code, sessions = _invoke(tmp_path, env, "session", "list")
        assert code == 0
        assert {item["name"] for item in sessions["data"]["sessions"]} >= {"board-a", "board-b"}

        code, conflict = _invoke(
            tmp_path,
            env,
            "--session",
            "board-c",
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
            "connect",
            "--host",
            "127.0.0.1",
            "--gdb-port",
            str(first.getsockname()[1]),
        )
        assert code == 2
        assert "already owned by session 'board-a'" in conflict["error"]["message"]

        code, status = _invoke(tmp_path, env, "--session", "board-a", "session", "status")
        assert code == 0 and status["data"]["active"] is True
        code, _ = _invoke(tmp_path, env, "--session", "board-a", "session", "stop")
        assert code == 0
        code, status = _invoke(tmp_path, env, "--session", "board-b", "session", "status")
        assert code == 0 and status["data"]["active"] is True
    finally:
        _invoke(tmp_path, env, "--session", "board-a", "session", "stop")
        _invoke(tmp_path, env, "--session", "board-b", "session", "stop")
        first.close()
        second.close()


def test_active_gdb_command_can_be_cancelled_from_another_cli_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    server = _listener()
    try:
        _start(
            tmp_path,
            env,
            server.getsockname()[1],
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
        )
        initial = read_session()
        assert initial and initial["gdb_pid"]
        command = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "embedded_debug.cli",
                "--json",
                "variable",
                "read",
                "hang_value",
            ],
            cwd=tmp_path,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if '-data-evaluate-expression "hang_value"' in (tmp_path / "gdb.log").read_text():
                break
            time.sleep(0.02)
        else:
            command.kill()
            raise AssertionError("long-running GDB/MI operation did not start")

        code, denied = _invoke(tmp_path, env, "session", "cancel")
        assert code == 2
        assert "authorize-run" in denied["error"]["message"]
        code, cancelled = _invoke(tmp_path, env, "session", "cancel", "--authorize-run")
        assert code == 0, cancelled
        stdout, _stderr = command.communicate(timeout=5)
        operation = json.loads(stdout)
        assert command.returncode == 2
        assert "interrupted" in operation["error"]["message"]

        code, status = _invoke(tmp_path, env, "session", "status")
        assert code == 0
        assert status["data"]["active"] is True
        assert status["data"]["target_state"] == "halted"
        assert status["data"]["gdb_pid"] == initial["gdb_pid"]
    finally:
        _stop(tmp_path, env)
        server.close()


def test_session_conflict_and_unauthorized_hardware_operations_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    first, second = _listener(), _listener()
    try:
        _start(
            tmp_path,
            env,
            first.getsockname()[1],
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
        )
        code, conflict = _invoke(
            tmp_path,
            env,
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
            "connect",
            "--host",
            "127.0.0.1",
            "--gdb-port",
            str(second.getsockname()[1]),
        )
        assert code == 2
        assert "different debug session" in conflict["error"]["message"]

        dangerous = [
            ("reset",),
            ("flash",),
            ("memory-write", "0x20000000", "--value", "1"),
            ("continue",),
            ("step",),
        ]
        for command in dangerous:
            code, response = _invoke(tmp_path, env, *command)
            assert code == 2
            assert response["error"]["code"] == "AUTHORIZATION_REQUIRED"

        log = (tmp_path / "gdb.log").read_text()
        assert "monitor reset halt" not in log
        assert 'console "load"' not in log
        assert "-data-write-memory-bytes" not in log
        assert "-exec-continue" not in log
    finally:
        _stop(tmp_path, env)
        first.close()
        second.close()


def test_gdb_timeout_and_abnormal_exit_can_be_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    crash_file = tmp_path / "crash-on"
    env["EDBG_FAKE_GDB_CRASH_FILE"] = str(crash_file)
    env["EDBG_FAKE_GDB_SLOW_DELAY"] = "1.0"
    monkeypatch.setenv("EDBG_FAKE_GDB_CRASH_FILE", str(crash_file))
    monkeypatch.setenv("EDBG_FAKE_GDB_SLOW_DELAY", "1.0")
    server = _listener()
    try:
        _start(
            tmp_path,
            env,
            server.getsockname()[1],
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
            "--gdb-timeout",
            "0.5",
        )
        code, timeout = _invoke(tmp_path, env, "variable", "read", "slow_value")
        assert code == 2
        assert "timed out" in timeout["error"]["message"]

        code, recovered = _invoke(tmp_path, env, "session", "recover")
        assert code == 0, recovered
        code, fast = _invoke(tmp_path, env, "variable", "read", "fast_value")
        assert code == 0, fast
        assert fast["data"]["value"] == "7"

        crash_file.write_text("-data-evaluate-expression", encoding="utf-8")
        code, crashed = _invoke(tmp_path, env, "variable", "read", "crash_value")
        assert code == 2
        assert crashed["success"] is False
        crash_file.unlink()

        _wait_for(tmp_path, env, lambda item: item["active"] is False)
        code, recovered = _invoke(tmp_path, env, "session", "recover")
        assert code == 0, recovered
        code, fast = _invoke(tmp_path, env, "variable", "read", "fast_value")
        assert code == 0, fast
        starts = [
            line
            for line in (tmp_path / "gdb.log").read_text().splitlines()
            if line.startswith("START ")
        ]
        assert len(starts) == 3
    finally:
        _stop(tmp_path, env)
        server.close()


def test_external_gdb_server_disconnect_is_detected_in_band_and_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    link_file = tmp_path / "link-down"
    env["EDBG_FAKE_GDB_LINK_FILE"] = str(link_file)
    monkeypatch.setenv("EDBG_FAKE_GDB_LINK_FILE", str(link_file))
    server = _listener()
    try:
        _start(
            tmp_path,
            env,
            server.getsockname()[1],
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
        )
        link_file.write_text("disconnected", encoding="utf-8")
        code, status = _invoke(tmp_path, env, "session", "status")
        assert code == 0
        assert status["data"]["active"] is False
        assert status["data"]["target_link"] == "disconnected"
        link_file.unlink()

        code, recovered = _invoke(tmp_path, env, "session", "recover")
        assert code == 0, recovered
        code, status = _invoke(tmp_path, env, "session", "status")
        assert code == 0 and status["data"]["target_link"] == "verified"
    finally:
        _stop(tmp_path, env)
        server.close()


def test_gdb_health_probe_timeout_is_unknown_not_disconnected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    timeout_file = tmp_path / "health-timeout"
    env["EDBG_FAKE_GDB_REGISTER_TIMEOUT_FILE"] = str(timeout_file)
    monkeypatch.setenv("EDBG_FAKE_GDB_REGISTER_TIMEOUT_FILE", str(timeout_file))
    server = _listener()
    try:
        _start(
            tmp_path,
            env,
            server.getsockname()[1],
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
            "--gdb-timeout",
            "0.08",
        )
        timeout_file.touch()
        code, status = _invoke(tmp_path, env, "session", "status")
        assert code == 0
        assert status["data"]["active"] is True
        assert status["data"]["target_state"] == "unknown"
        assert status["data"]["target_link"] == "unknown"
        assert "timed out" in status["data"]["target_link_error"]
        timeout_file.unlink()

        code, recovered = _invoke(tmp_path, env, "session", "recover")
        assert code == 0, recovered
        code, status = _invoke(tmp_path, env, "session", "status")
        assert code == 0 and status["data"]["target_link"] == "verified"
    finally:
        _stop(tmp_path, env)
        server.close()


def test_killed_manager_reaps_only_its_orphaned_gdb_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    server = _listener()
    try:
        _start(
            tmp_path,
            env,
            server.getsockname()[1],
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
        )
        record = read_session()
        assert record and gdb_identity_matches(record)
        daemon_pid, gdb_pid = record["daemon_pid"], record["gdb_pid"]
        psutil.Process(daemon_pid).kill()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and process_alive(daemon_pid):
            time.sleep(0.02)
        assert not process_alive(daemon_pid)

        code, status = _invoke(tmp_path, env, "session", "status")
        assert code == 0 and status["data"]["active"] is False
        assert not process_alive(gdb_pid)
        assert read_session() is None

        _start(
            tmp_path,
            env,
            server.getsockname()[1],
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
        )
        starts = [
            line
            for line in (tmp_path / "gdb.log").read_text().splitlines()
            if line.startswith("START ")
        ]
        assert len(starts) == 2
    finally:
        _stop(tmp_path, env)
        server.close()


def test_owned_openocd_failure_is_detected_and_restarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(monkeypatch, tmp_path)
    env["EDBG_MOCK_OPENOCD_ARGS"] = str(tmp_path / "openocd.args")
    monkeypatch.setenv("EDBG_MOCK_OPENOCD_ARGS", env["EDBG_MOCK_OPENOCD_ARGS"])
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    try:
        _start(
            tmp_path,
            env,
            port,
            "--gdb",
            str(MOCKS / "fake_gdb.py"),
            "--openocd",
            str(MOCKS / "fake_openocd.py"),
            "--gdb-timeout",
            "1",
            "--interface",
            "interface/mock.cfg",
            "--target",
            "target/mock.cfg",
        )
        record = read_session()
        assert record and record["openocd_owned"]
        previous_pid = record["openocd_pid"]
        psutil.Process(previous_pid).terminate()
        state = _wait_for(tmp_path, env, lambda item: item["target_state"] == "disconnected")
        assert state["active"] is False

        code, denied = _invoke(tmp_path, env, "session", "recover")
        assert code != 0
        assert "authorize-openocd-restart" in denied["error"]["message"]
        code, recovered = _invoke(
            tmp_path, env, "session", "recover", "--authorize-openocd-restart"
        )
        assert code == 0, recovered
        updated = read_session()
        assert updated and updated["openocd_pid"] != previous_pid
        code, status = _invoke(tmp_path, env, "session", "status")
        assert code == 0 and status["data"]["active"] is True
    finally:
        _stop(tmp_path, env)
