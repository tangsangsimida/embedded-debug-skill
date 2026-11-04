import sys
import time

import psutil
import pytest

from embedded_debug.services.output import run_external


def test_external_timeout_keeps_output_and_reports_timeout(tmp_path, capsys) -> None:
    started = time.monotonic()
    result = run_external(
        [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"],
        tmp_path,
        True,
        0.3,
    )
    assert time.monotonic() - started < 10
    assert result.timed_out
    assert "started" in result.stdout
    assert "started" in capsys.readouterr().err


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_external_timeout_is_rejected(tmp_path, timeout) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        run_external([sys.executable, "-c", "pass"], tmp_path, True, timeout)


def test_timeout_reaps_owned_child_process(tmp_path, capsys) -> None:
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "print(child.pid,flush=True); time.sleep(30)"
    )
    result = run_external([sys.executable, "-c", script], tmp_path, True, 1.0)
    capsys.readouterr()
    assert result.timed_out
    pid = int(result.stdout.strip())
    try:
        assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        pass
