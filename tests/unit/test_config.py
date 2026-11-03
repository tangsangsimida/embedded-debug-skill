from pathlib import Path

import pytest

from embedded_debug.config import load_config


def test_user_supplied_configs_and_loopback_default(tmp_path: Path) -> None:
    file = tmp_path / "edbg.toml"
    file.write_text(
        '[openocd]\ninterface="interface/a.cfg"\ntarget="target/b.cfg"\n[project]\nelf="app.elf"\n'
    )
    cfg = load_config(file)
    assert cfg.openocd.interface == "interface/a.cfg"
    assert cfg.elf == "app.elf"
    assert cfg.openocd.host == "127.0.0.1"


def test_reject_non_loopback_openocd(tmp_path: Path) -> None:
    file = tmp_path / "edbg.toml"
    file.write_text('[openocd]\nhost="0.0.0.0"\n')
    with pytest.raises(ValueError, match="loopback"):
        load_config(file)


def test_reject_unknown_keys(tmp_path: Path) -> None:
    file = tmp_path / "edbg.toml"
    file.write_text('[target]\nchip="STM32"\n')
    with pytest.raises(ValueError, match="unknown configuration section"):
        load_config(file)


def test_reject_invalid_command_types(tmp_path: Path) -> None:
    file = tmp_path / "edbg.toml"
    file.write_text('[build]\ncommand=["make", 3]\n')
    with pytest.raises(ValueError, match="array of strings"):
        load_config(file)


def test_reject_non_table_sections(tmp_path: Path) -> None:
    file = tmp_path / "edbg.toml"
    file.write_text('tools="/usr/bin"\n')
    with pytest.raises(ValueError, match="TOML table"):
        load_config(file)
