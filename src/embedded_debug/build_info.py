"""Version and reproducible Python-source fingerprint for Skill/CLI binding."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import __version__


def source_digest(package_dir: Path | None = None) -> str:
    root = package_dir or Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def build_info() -> dict[str, Any]:
    return {"version": __version__, "source_digest": source_digest()}
