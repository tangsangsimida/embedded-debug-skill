"""Select the narrow platform adapter used by runtime services."""

from __future__ import annotations

import sys
from functools import lru_cache

from .base import BasePlatform
from .linux import LinuxPlatform
from .macos import MacOSPlatform
from .windows import WindowsPlatform


@lru_cache(maxsize=1)
def get_platform() -> BasePlatform:
    if sys.platform == "win32":
        return WindowsPlatform()
    if sys.platform == "darwin":
        return MacOSPlatform()
    if sys.platform.startswith("linux"):
        return LinuxPlatform()
    return BasePlatform()
