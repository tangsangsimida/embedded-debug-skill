#!/usr/bin/env python3
"""Require package and Skill metadata to share one version."""

import re
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[3]
package = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
skill = (root / "skills/embedded-debug/SKILL.md").read_text()
match = re.search(r'(?m)^  version: "([^"]+)"$', skill)
if not match or match.group(1) != package:
    raise SystemExit(
        f"Version mismatch: package={package}, skill={match.group(1) if match else 'missing'}"
    )
launcher = (root / "skills/embedded-debug/scripts/run_edbg.py").read_text()
pinned = re.search(r'(?m)^PINNED_VERSION = "([^"]+)"$', launcher)
if not pinned or pinned.group(1) != package:
    raise SystemExit(
        f"Version mismatch: package={package}, launcher={pinned.group(1) if pinned else 'missing'}"
    )
print(f"Package, Skill, and launcher versions match: {package}")
