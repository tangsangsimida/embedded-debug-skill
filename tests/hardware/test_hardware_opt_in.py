"""Real hardware tests are opt-in and use a user-supplied existing project setup."""

from __future__ import annotations

import os

import pytest

from tests.hardware.hil_runner import load_config, run_hil

CONFIG = os.environ.get("EDBG_HARDWARE_CONFIG")
pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("EDBG_HARDWARE_TESTS") != "1" or not CONFIG,
        reason="set EDBG_HARDWARE_TESTS=1 and EDBG_HARDWARE_CONFIG to opt in",
    ),
]


def test_existing_project_debug_loop() -> None:
    assert CONFIG is not None
    result = run_hil(load_config(CONFIG))
    assert result["connect"]["target"]["connection"] == "connected"
    assert isinstance(result["registers"].get("data"), list)
    assert "backtrace" in result
