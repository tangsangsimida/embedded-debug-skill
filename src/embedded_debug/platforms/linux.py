"""Linux-specific process controls."""

from __future__ import annotations

import os
from pathlib import Path

from .base import BasePlatform


class LinuxPlatform(BasePlatform):
    name = "linux"

    def legacy_process_identity_matches(
        self, *, pid: int, created: object, executable: str
    ) -> bool | None:
        """Validate schema-2 `/proc` start ticks for safe in-place upgrades."""
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            fields = stat[stat.rfind(")") + 2 :].split()
            if fields[19] != str(created):
                return False
            uid_line = next(
                line
                for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                if line.startswith("Uid:")
            )
            getuid = getattr(os, "getuid", None)
            if getuid is None or int(uid_line.split()[1]) != getuid():
                return False
            actual = Path(f"/proc/{pid}/exe").resolve(strict=True)
            expected = Path(executable).resolve(strict=True)
            return actual == expected
        except (OSError, StopIteration, ValueError, IndexError):
            return None
