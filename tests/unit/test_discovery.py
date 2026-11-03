import json
from pathlib import Path

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
    assert result["elf_candidates"][0]["path"].endswith("build/app.elf")


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
