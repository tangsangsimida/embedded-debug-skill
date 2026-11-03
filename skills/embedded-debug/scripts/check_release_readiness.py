#!/usr/bin/env python3
"""Fail closed when a stable release has unmet acceptance criteria."""

import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[3]
release = tomllib.loads((root / "release-readiness.toml").read_text())["release"]
if not release["stable_release_ready"] or release["blockers"]:
    print("Stable Release blocked:")
    for blocker in release["blockers"]:
        print(f"- {blocker}")
    raise SystemExit(1)
print("Stable Release readiness approved")
