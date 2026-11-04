import threading
import time
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


def test_interrupt_result_alone_does_not_confirm_stop(monkeypatch) -> None:
    monkeypatch.setenv("EDBG_FAKE_GDB_NO_STOP", "1")
    transport = MITransport(str(MOCK_GDB), timeout=1)
    try:
        transport.start()
        transport.state = "running"
        transport.command("-exec-interrupt --all")
        assert not transport.wait_for_state("halted", 0.05)
        assert transport.state == "running"
    finally:
        transport.close()


def test_async_flood_is_bounded_and_does_not_block_results() -> None:
    transport = MITransport(str(MOCK_GDB), timeout=5)
    try:
        transport.start()
        transport.command("-test-flood")
        assert len(transport.async_records) == 500
        assert transport.async_records_dropped == 4500
        assert transport._records.empty()
        assert transport.command("-gdb-show architecture").results["value"] == "arm"
    finally:
        transport.close()


def test_idle_async_flood_is_bounded_and_later_commands_succeed() -> None:
    transport = MITransport(str(MOCK_GDB), timeout=5)
    try:
        transport.start()
        transport.command("-test-idle-flood")
        deadline = time.monotonic() + 5
        while transport.async_records_dropped < 9500 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert transport.async_records_dropped == 9500
        assert transport._records.empty()
        assert transport.command("-gdb-show architecture").results["value"] == "arm"
    finally:
        transport.close()


def test_late_result_is_discarded_after_timeout() -> None:
    transport = MITransport(str(MOCK_GDB), timeout=1)
    try:
        transport.start()
        with pytest.raises(GDBTimeout):
            transport.command('-data-evaluate-expression "slow"', timeout=0.05)
        result = transport.command('-data-evaluate-expression "fast"')
        assert result.results["value"] == "7"
        assert transport.unmatched_results_dropped == 1
        assert transport._records.empty()
    finally:
        transport.close()


def test_reconnect_restarts_debugger_process() -> None:
    transport = MITransport(str(MOCK_GDB), timeout=1)
    transport.start()
    first_pid = transport.process.pid
    transport.reconnect()
    assert transport.process is not None
    assert transport.process.pid != first_pid
    assert transport.command("-gdb-show architecture").results["value"] == "arm"
    transport.close()


def test_delayed_old_reader_stays_bound_to_its_process(monkeypatch) -> None:
    transport = MITransport(str(MOCK_GDB), timeout=2)
    original_start = threading.Thread.start
    delayed_readers: list[threading.Thread] = []

    def delay_first_stdout_reader(thread: threading.Thread) -> None:
        target = getattr(thread, "_target", None)
        owner = getattr(target, "__self__", None)
        if (
            owner is transport
            and getattr(target, "__name__", "") == "_read_stdout"
            and not delayed_readers
        ):
            delayed_readers.append(thread)
            return
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", delay_first_stdout_reader)
    try:
        transport.start()
        first_process = transport.process
        transport.reconnect()
        second_process = transport.process
        assert first_process is not None and second_process is not None
        assert first_process.pid != second_process.pid

        assert len(delayed_readers) == 1
        original_start(delayed_readers[0])
        assert transport.command("-gdb-show architecture").results["value"] == "arm"
        assert transport.state != "disconnected"
    finally:
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
