import json
from pathlib import Path

from embedded_debug.cli import run


def test_pytest_imports_embedded_debug_from_this_checkout() -> None:
    import embedded_debug

    source = (Path(__file__).resolve().parents[2] / "src").resolve()
    assert Path(embedded_debug.__file__).resolve().is_relative_to(source)


def test_doctor_json_reports_without_hardware(monkeypatch, capsys) -> None:
    monkeypatch.setattr("embedded_debug.doctor.shutil.which", lambda _: None)
    assert run(["--json", "doctor"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["success"] is True
    assert data["command"] == "doctor"
    assert "tools" in data["data"]


def test_guarded_memory_write_refuses_without_authorization(capsys) -> None:
    assert run(["--json", "memory-write", "0x20000000", "--value", "1"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "AUTHORIZATION_REQUIRED"


def test_nested_command_syntax(capsys) -> None:
    assert run(["config", "validate", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["command"] == "config-validate"


def test_environment_inspect_and_plan_are_json_diagnostics(capsys) -> None:
    assert run(["--json", "environment", "inspect"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["success"] is True
    assert "missing_debug_tools" in inspected["data"]
    assert run(["environment", "plan", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["command"] == "environment-plan"
    assert "actions" in plan["data"]


def test_environment_install_cli_requires_authorization(capsys) -> None:
    assert run(["--json", "environment", "install-cli", "--source", "example==0.1"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "AUTHORIZATION_REQUIRED"


def test_environment_path_changes_require_authorization_and_emit_json(
    tmp_path, monkeypatch, capsys
) -> None:
    from pathlib import Path

    home = tmp_path / "home"
    home.mkdir()
    tool_dir = tmp_path / "existing-bin"
    tool_dir.mkdir()
    rc_file = home / ".bashrc"
    rc_file.write_text("# user config\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    add_args = [
        "--json",
        "environment",
        "path-add",
        "--directory",
        str(tool_dir),
        "--shell",
        "bash",
    ]
    assert run(add_args) == 2
    denied = json.loads(capsys.readouterr().out)
    assert denied["error"]["code"] == "AUTHORIZATION_REQUIRED"
    assert rc_file.read_text(encoding="utf-8") == "# user config\n"

    assert run([*add_args, "--authorize"]) == 0
    added = json.loads(capsys.readouterr().out)
    assert added["success"] is True
    assert added["data"]["changed"] is True
    assert str(tool_dir) in rc_file.read_text(encoding="utf-8")

    assert (
        run(
            [
                "--json",
                "environment",
                "path-remove",
                "--shell",
                "bash",
                "--authorize",
            ]
        )
        == 0
    )
    removed = json.loads(capsys.readouterr().out)
    assert removed["success"] is True
    assert removed["data"]["changed"] is True
    assert rc_file.read_text(encoding="utf-8") == "# user config\n"


def test_connect_reuses_existing_server_with_mock_gdb(tmp_path, monkeypatch, capsys) -> None:
    import socket
    from pathlib import Path

    fake_gdb = Path(__file__).parents[1] / "mocks" / "fake_gdb.py"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert (
            run(
                [
                    "--json",
                    "--gdb",
                    str(fake_gdb),
                    "connect",
                    "--host",
                    "127.0.0.1",
                    "--gdb-port",
                    str(port),
                    "--reuse-server",
                ]
            )
            == 0
        )
    data = json.loads(capsys.readouterr().out)
    assert data["target"]["connection"] == "connected"
    assert data["target"]["architecture"] == "arm"
    assert run(["session", "stop", "--json"]) == 0
    capsys.readouterr()


def test_nested_session_start_status_stop_reuses_external_server(
    tmp_path, monkeypatch, capsys
) -> None:
    import socket
    from pathlib import Path

    fake_gdb = Path(__file__).parents[1] / "mocks" / "fake_gdb.py"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert (
            run(
                [
                    "--gdb",
                    str(fake_gdb),
                    "session",
                    "start",
                    "--host",
                    "127.0.0.1",
                    "--gdb-port",
                    str(port),
                    "--json",
                ]
            )
            == 0
        )
        started = json.loads(capsys.readouterr().out)
        assert started["data"]["session"] == "started"
        assert started["data"]["architecture"] == "arm"
        assert run(["--json", "session", "status"]) == 0
        status = json.loads(capsys.readouterr().out)
        assert status["data"]["active"] is True
        assert run(["session", "stop", "--json"]) == 0
        stopped = json.loads(capsys.readouterr().out)
        assert stopped["data"]["stopped"] is True
        assert server.fileno() >= 0


def test_connect_uses_swappable_openocd_configs_without_source_edits(
    tmp_path, monkeypatch, capsys
) -> None:
    import socket
    from pathlib import Path

    fake_gdb = Path(__file__).parents[1] / "mocks" / "fake_gdb.py"
    fake_openocd = Path(__file__).parents[1] / "mocks" / "fake_openocd.py"
    args_file = tmp_path / "openocd-args.txt"
    monkeypatch.setenv("EDBG_MOCK_OPENOCD_ARGS", str(args_file))
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    for interface, target in [
        ("interface/adapter-a.cfg", "target/core-a.cfg"),
        ("interface/adapter-b.cfg", "target/core-b.cfg"),
    ]:
        assert (
            run(
                [
                    "--json",
                    "--gdb",
                    str(fake_gdb),
                    "--openocd",
                    str(fake_openocd),
                    "connect",
                    "--interface",
                    interface,
                    "--target",
                    target,
                    "--gdb-port",
                    str(port),
                ]
            )
            == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["success"] is True
        captured = args_file.read_text()
        assert interface in captured
        assert target in captured
        assert f"gdb_port {port}" in captured
        assert run(["session", "stop", "--json"]) == 0
        capsys.readouterr()


def test_skill_launcher_never_installs_without_authorization(monkeypatch, capsys, tmp_path) -> None:
    import importlib.metadata
    import runpy
    from pathlib import Path

    script = Path(__file__).parents[2] / "skills/embedded-debug/scripts/run_edbg.py"
    namespace = runpy.run_path(str(script))
    # CI may have exercised the authorized bootstrap earlier in the same job.
    # Keep this refusal test independent from any cached CLI installed there.
    monkeypatch.setenv("EDBG_CACHE_HOME", str(tmp_path / "empty-cache"))

    def missing_package(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(namespace["importlib"].metadata, "version", missing_package)
    monkeypatch.setattr(namespace["shutil"], "which", lambda _name: None)
    assert namespace["main"](["doctor", "--json"]) == 2
    assert "explicit user authorization" in capsys.readouterr().err


def test_json_build_keeps_json_clean_and_logs_on_stderr(tmp_path, capsys) -> None:
    config = tmp_path / "edbg.toml"
    config.write_text(
        '[build]\ncommand=["python3", "-c", "print(\\"BUILD-LOG\\")"]\ndirectory="."\n'
    )
    assert run(["--json", "--config", str(config), "build", "--authorize-command"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["success"] is True
    assert "BUILD-LOG" in captured.err
