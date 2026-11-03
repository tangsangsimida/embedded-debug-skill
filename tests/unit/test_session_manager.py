from __future__ import annotations

import os
import sys

import pytest

from embedded_debug import session_manager
from embedded_debug.session_store import save_session, session_path


def _unverifiable_record() -> dict[str, object]:
    return {
        "schema": 2,
        "session_id": "existing-session",
        "daemon_pid": os.getpid(),
        "process_start_time": "unverifiable-start-time",
        "python_executable": sys.executable,
        "gdb_pid": 123456789,
        "gdb_start_time": "unknown",
        "gdb_process_executable": sys.executable,
    }


def test_status_does_not_delete_an_unverifiable_session_record(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    save_session(_unverifiable_record())
    monkeypatch.setattr(session_manager, "daemon_identity_matches", lambda _info: False)
    monkeypatch.setattr(session_manager, "daemon_identity_is_stale", lambda _info: False)

    result = session_manager.session_status()

    assert result["target_state"] == "unknown"
    assert "identity is unavailable" in result["error"]
    assert session_path().is_file()


def test_start_refuses_to_compete_with_an_unverifiable_session(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    save_session(_unverifiable_record())
    monkeypatch.setattr(session_manager, "daemon_identity_matches", lambda _info: False)
    monkeypatch.setattr(session_manager, "daemon_identity_is_stale", lambda _info: False)
    monkeypatch.setattr(
        session_manager.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("must not start a competing manager"),
    )

    with pytest.raises(session_manager.SessionManagerError, match="cannot be verified"):
        session_manager.start_session({})
    assert session_path().is_file()
