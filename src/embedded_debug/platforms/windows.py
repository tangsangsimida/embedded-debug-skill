"""Windows-specific process controls."""

from __future__ import annotations

import signal
import subprocess

from .base import BasePlatform


class WindowsPlatform(BasePlatform):
    name = "windows"

    def creation_options(self) -> dict[str, object]:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}

    def interrupt_debugger(self, process: subprocess.Popen[str]) -> None:
        try:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT))
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "Could not send a console interrupt to GDB; run edbg from a console session"
            ) from exc
