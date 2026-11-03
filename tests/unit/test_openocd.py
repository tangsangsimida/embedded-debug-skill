from embedded_debug.openocd.manager import OpenOCDManager


def test_port_detection_is_best_effort(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise OSError("closed")

    monkeypatch.setattr("embedded_debug.openocd.manager.socket.create_connection", fail)
    assert not OpenOCDManager.port_open("127.0.0.1", 3333)


def test_openocd_start_failure_is_reported_and_reaped(tmp_path, monkeypatch) -> None:
    import pytest

    from embedded_debug.openocd.manager import OpenOCDError

    fake = tmp_path / "openocd-fail"
    fake.write_text("#!/bin/sh\nexit 9\n")
    fake.chmod(0o755)
    monkeypatch.setattr("embedded_debug.openocd.manager.OpenOCDManager.port_open", lambda *a: False)
    manager = OpenOCDManager(str(fake), log_file=tmp_path / "openocd.log")
    with pytest.raises(OpenOCDError, match="status 9"):
        manager.start(timeout=1)
    manager.stop()
    assert manager.process is None
