from pathlib import Path

import pytest

from embedded_debug.gdb.controller import GDBController
from embedded_debug.gdb.mi_transport import GDBTimeout, MITransport

MOCK_GDB = Path(__file__).parents[1] / "mocks" / "fake_gdb.py"


def test_mock_gdb_async_state_and_capabilities() -> None:
    transport = MITransport(str(MOCK_GDB), timeout=1)
    controller = GDBController(transport)
    try:
        result = controller.connect()
        assert result["architecture"] == "arm"
        assert transport.state == "halted"
        assert controller.registers == ["r0", "pc"]
        assert controller.capabilities.memory_read == "unknown"
        assert controller.capabilities.rtos_awareness == "unknown"
    finally:
        transport.close()


def test_command_timeout_and_cleanup() -> None:
    transport = MITransport(str(MOCK_GDB), timeout=0.05)
    transport.start()
    with pytest.raises(GDBTimeout):
        transport.command("-hang")
    transport.close()
    assert transport.state == "disconnected"


def test_reconnect_restarts_debugger_process() -> None:
    transport = MITransport(str(MOCK_GDB), timeout=1)
    transport.start()
    first_pid = transport.process.pid
    transport.reconnect()
    assert transport.process is not None
    assert transport.process.pid != first_pid
    assert transport.command("-gdb-show architecture").results["value"] == "arm"
    transport.close()


def test_stderr_is_drained_concurrently_without_blocking_mi(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EDBG_FAKE_GDB_STDERR_BYTES", "262144")
    transport = MITransport(str(MOCK_GDB), timeout=2)
    try:
        transport.start()
        assert transport.command("-gdb-show architecture").results["value"] == "arm"
        assert transport.stderr_tail
    finally:
        transport.close()
