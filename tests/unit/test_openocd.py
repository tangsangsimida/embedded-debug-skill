import sys

from embedded_debug.openocd.manager import OpenOCDManager


def test_port_detection_is_best_effort(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise OSError("closed")

    monkeypatch.setattr("embedded_debug.openocd.manager.socket.create_connection", fail)
    assert not OpenOCDManager.port_open("127.0.0.1", 3333)


def test_openocd_start_failure_is_reported_and_reaped(tmp_path, monkeypatch) -> None:
    import pytest

    from embedded_debug.openocd.manager import OpenOCDError

    fake = tmp_path / "openocd-fail.py"
    fake.write_text(f"#!{sys.executable}\nraise SystemExit(9)\n")
    monkeypatch.setattr("embedded_debug.openocd.manager.OpenOCDManager.port_open", lambda *a: False)
    manager = OpenOCDManager(str(fake), log_file=tmp_path / "openocd.log")
    with pytest.raises(OpenOCDError, match="status 9"):
        manager.start(timeout=1)
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
