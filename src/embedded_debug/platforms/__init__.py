"""Small operating-system boundary for paths, locks, and owned processes."""

from .detection import get_platform

__all__ = ["get_platform"]
