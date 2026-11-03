import json
from pathlib import Path
from types import SimpleNamespace

from embedded_debug.discovery import discover_project, openocd_launch_options


def test_discovers_vscode_openocd_and_elf(tmp_path: Path, monkeypatch) -> None:
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    (vscode / "launch.json").write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "configurations": [
                    {
                        "name": "firmware",
                        "servertype": "openocd",
                        "configFiles": ["interface/custom.cfg", "target/custom.cfg"],
                        "gdbTarget": "localhost:3338",
                        "executable": "${workspaceFolder}/build/app.elf",
                    }
                ],
            }
        )
    )
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "app.elf").write_bytes(b"elf")
    monkeypatch.setattr("embedded_debug.discovery.OpenOCDManager.port_open", lambda *a: False)
    result = discover_project(tmp_path)
    options = openocd_launch_options(result)
    assert options["config_files"] == ["interface/custom.cfg", "target/custom.cfg"]
    assert options["host"] == "localhost"
    assert options["gdb_port"] == 3338
    assert Path(result["elf_candidates"][0]["path"]).parts[-2:] == ("build", "app.elf")


def test_discovers_build_without_edbg_config(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "CMakeLists.txt").write_text("project(example)")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr("embedded_debug.discovery.OpenOCDManager.port_open", lambda *a: False)
    result = discover_project(tmp_path)
    assert result["build"]["argv"] == ["cmake", "--build", "build"]


def test_parses_jsonc_comments_and_flash_task(tmp_path: Path, monkeypatch) -> None:
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    (vscode / "tasks.json").write_text(
        """
        {
          // Existing build and flash operations are reusable.
          "version": "2.0.0",
          "tasks": [
            {"label": "build firmware", "command": "make", "args": ["all"],},
            {"label": "flash firmware", "command": "make", "args": ["flash"],},
          ],
        }
        """
    )
    monkeypatch.setattr("embedded_debug.discovery.OpenOCDManager.port_open", lambda *a: False)
    result = discover_project(tmp_path)
    assert result["build"]["argv"] == ["make", "all"]
    assert result["flash"]["argv"] == ["make", "flash"]


def test_discovers_openocd_command_arguments_and_gdb_path(tmp_path: Path, monkeypatch) -> None:
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    (vscode / "launch.json").write_text(
        json.dumps(
            {
                "configurations": [
                    {
                        "name": "command-driven",
                        "serverpath": "/custom/openocd",
                        "gdbExecutable": "/custom/gdb",
                        "serverArgs": [
                            "-f",
                            "interface/user.cfg",
                            "-f",
                            "target/user.cfg",
                            "-c",
                            "gdb_port 4555",
                        ],
                    }
                ]
            }
        )
    )
    monkeypatch.setattr("embedded_debug.discovery.OpenOCDManager.port_open", lambda *a: False)
    result = discover_project(tmp_path)
    options = openocd_launch_options(result)
    assert options["config_files"] == ["interface/user.cfg", "target/user.cfg"]
    assert options["gdb_port"] == 4555
    assert options["gdb_executable"] == "/custom/gdb"
    assert options["server_path"] == "/custom/openocd"


def test_discovers_literal_openocd_command_from_makefile(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "Makefile").write_text(
        "debug-server:\n\t@openocd -f interface/cmsis-dap.cfg "
        '-f board/custom.cfg -c "gdb_port 4555"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("embedded_debug.discovery.OpenOCDManager.port_open", lambda *a: False)
    result = discover_project(tmp_path)
    options = openocd_launch_options(result)
    assert options["config_files"] == ["interface/cmsis-dap.cfg", "board/custom.cfg"]
    assert options["gdb_port"] == 4555
    assert result["openocd_candidates"][0]["source"].endswith("Makefile:2")


def test_does_not_guess_interpolated_openocd_script_arguments(tmp_path: Path, monkeypatch) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "debug.sh").write_text(
        "openocd -f \"$OPENOCD_CONFIG\" -c 'gdb_port 4444'\n", encoding="utf-8"
    )
    monkeypatch.setattr("embedded_debug.discovery.OpenOCDManager.port_open", lambda *a: False)
    result = discover_project(tmp_path)
    assert result["openocd_candidates"][0]["requires_review"] is True
    assert openocd_launch_options(result) == {}


def test_discovers_nonstandard_openocd_loopback_listener(tmp_path: Path, monkeypatch) -> None:
    address = SimpleNamespace(ip="127.0.0.1", port=4556)
    listener = SimpleNamespace(status="LISTEN", laddr=address, pid=42)
    monkeypatch.setattr("embedded_debug.discovery.psutil.net_connections", lambda **_: [listener])
    monkeypatch.setattr(
        "embedded_debug.discovery.psutil.Process",
        lambda pid: SimpleNamespace(exe=lambda: "/tools/openocd"),
    )
    monkeypatch.setattr(
        "embedded_debug.discovery.OpenOCDManager.port_open",
        lambda host, port: port == 4556,
    )
    result = discover_project(tmp_path)
    assert result["active_gdb_servers"] == [
        {"host": "127.0.0.1", "port": 4556, "status": "reachable"}
    ]


def test_discovers_existing_make_and_cmake_flash_targets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("embedded_debug.discovery.OpenOCDManager.port_open", lambda *a: False)
    (tmp_path / "Makefile").write_text("all:\n\t@echo build\nflash:\n\t@openocd\n")
    result = discover_project(tmp_path)
    assert result["flash"]["argv"] == ["make", "flash"]

    (tmp_path / "Makefile").unlink()
    (tmp_path / "CMakeLists.txt").write_text(
        "project(example)\nadd_custom_target(flash COMMAND openocd)\n"
    )
    (tmp_path / "build").mkdir()
    result = discover_project(tmp_path)
    assert result["flash"]["argv"] == ["cmake", "--build", "build", "--target", "flash"]
