#!/usr/bin/env python3
"""Validate Skill metadata and relative resource links with stdlib only."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def validate(root: Path) -> list[str]:
    path = root / "SKILL.md"
    if not path.is_file():
        return ["SKILL.md is missing"]
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return ["YAML frontmatter delimiters are missing"]
    header, body = text[4:].split("\n---\n", 1)
    values = dict(re.findall(r"^([a-zA-Z_-]+):\s*(.*)$", header, re.M))
    errors = []
    name = values.get("name", "").strip("\"'")
    if name != root.name or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        errors.append("name must match directory and Agent Skills naming rules")
    description = values.get("description", "").strip("\"'")
    if not description or len(description) > 1024:
        errors.append("description must be 1..1024 characters")
    for link in re.findall(r"\[[^]]+\]\(([^)]+)\)", body):
        if "://" not in link and not (root / link).is_file():
            errors.append(f"referenced file does not exist: {link}")
    for rel in re.findall(r"(?m)^\s*(scripts/[\w./-]+)", body):
        if not (root / rel).exists():
            errors.append(f"referenced script does not exist: {rel}")
    return errors


if __name__ == "__main__":
    problems = validate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[1])
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    print("Skill valid" if not problems else "Skill invalid")
    raise SystemExit(bool(problems))
