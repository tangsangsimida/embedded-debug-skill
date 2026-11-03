"""macOS-specific process controls."""

from __future__ import annotations

from .base import BasePlatform


class MacOSPlatform(BasePlatform):
    name = "macos"
