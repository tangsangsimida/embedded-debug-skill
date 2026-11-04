"""Pin test imports and child-process imports to this checkout's source tree."""

from __future__ import annotations

import os
import sys
from pathlib import Path

SOURCE_ROOT = (Path(__file__).resolve().parents[1] / "src").resolve()
source_text = str(SOURCE_ROOT)
if source_text not in sys.path:
    sys.path.insert(0, source_text)

# Integration tests launch Python from temporary working directories. An absolute
# path prevents those subprocesses from accidentally importing a stale installed wheel.
inherited = os.environ.get("PYTHONPATH", "")
entries = [item for item in inherited.split(os.pathsep) if item]
if source_text not in entries:
    os.environ["PYTHONPATH"] = os.pathsep.join([source_text, *entries])
