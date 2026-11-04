import socket
import sys
from pathlib import Path

import pytest

from embedded_debug.openocd.manager import OpenOCDError, OpenOCDManager


def test_port_detection_is_best_effort(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise OSError("closed")

    monkeypatch.setattr("embedded_debug.openocd.manager.socket.create_connection", fail)
    assert not OpenOCDManager.port_open("127.0.0.1", 3333)


def test_openocd_start_failure_is_reported_and_reaped(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "openocd-fail.py"
    fake.write_text(f"#!{sys.executable}\nraise SystemExit(9)\n")
    monkeypatch.setattr("embedded_debug.openocd.manager.OpenOCDManager.port_open", lambda *a: False)
    manager = OpenOCDManager(str(fake), log_file=tmp_path / "openocd.log")
    with pytest.raises(OpenOCDError, match="status 9"):
        manager.start(timeout=1, trusted_config=True)
    manager.stop()
    assert manager.process is None


def test_reused_server_does_not_keep_dead_child_as_owned(tmp_path, monkeypatch) -> None:
    class ExitedProcess:
        def poll(self) -> int:
            return 1

    manager = OpenOCDManager(log_file=tmp_path / "openocd.log")
    manager.process = ExitedProcess()  # type: ignore[assignment]
    manager._log_handle = (tmp_path / "stale.log").open("w")
    monkeypatch.setattr(
        "embedded_debug.openocd.manager.OpenOCDManager.port_open", lambda *args: True
    )

    manager.start()

    assert manager.process is None
    assert manager._log_handle is None


@pytest.mark.parametrize(
    "command", ["echo ok; bindto 0.0.0.0", "echo ok\nbindto 0.0.0.0", "script evil.cfg"]
)
def test_untrusted_tcl_is_never_launched(monkeypatch, command) -> None:
    monkeypatch.setattr(OpenOCDManager, "port_open", lambda *args: False)
    monkeypatch.setattr(
        "embedded_debug.openocd.manager.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("untrusted Tcl reached process launch"),
    )
    with pytest.raises(OpenOCDError, match="executable Tcl"):
        OpenOCDManager().start(commands=[command])


@pytest.mark.parametrize(
    "options",
    [
        {"config": "untrusted.cfg"},
        {"config_files": ["first.cfg", "untrusted.cfg"]},
        {"extra_args": ["-c", "echo ok; bindto 0.0.0.0"]},
        {"extra_args": ["-f", "untrusted.cfg", "-c", "init"]},
    ],
)
def test_untrusted_file_and_extra_arguments_are_never_launched(monkeypatch, options) -> None:
    monkeypatch.setattr(OpenOCDManager, "port_open", lambda *args: False)
    monkeypatch.setattr(
        "embedded_debug.openocd.manager.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("untrusted Tcl reached process launch"),
    )
    with pytest.raises(OpenOCDError, match="executable Tcl"):
        OpenOCDManager().start(**options)


def test_non_loopback_owned_listener_is_stopped(tmp_path, monkeypatch) -> None:
    from embedded_debug.platforms import get_platform

    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    monkeypatch.setenv("EDBG_MOCK_OPENOCD_ARGS", str(tmp_path / "args"))
    platform = get_platform()
    monkeypatch.setattr(platform, "tcp_listeners", lambda pid: [("0.0.0.0", port)])
    fake = Path(__file__).parents[1] / "mocks" / "fake_openocd.py"
    manager = OpenOCDManager(str(fake), port=port)
    with pytest.raises(OpenOCDError, match="non-loopback"):
        manager.start(trusted_config=True)
    assert manager.process is None
    assert not manager.port_open("127.0.0.1", port)
