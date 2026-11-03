import json
import os
import subprocess

import pytest

CONFIG = os.environ.get("EDBG_HARDWARE_CONFIG")
pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("EDBG_HARDWARE_TESTS") != "1" or not CONFIG,
        reason="set EDBG_HARDWARE_TESTS=1 and EDBG_HARDWARE_CONFIG to opt in",
    ),
]


def test_user_configured_target_register_read() -> None:
    environment = os.environ.copy()
    environment["EDBG_CONFIG"] = str(CONFIG)
    result = subprocess.run(
        ["edbg", "registers", "--json"],
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0, payload
    assert payload["success"] is True
    assert isinstance(payload["data"], list)
