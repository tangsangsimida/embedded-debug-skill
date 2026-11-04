#!/usr/bin/env python3
"""Verify Skill/CLI version and immutable Git source fingerprint alignment."""

from __future__ import annotations

import hashlib
import re
import subprocess
import tomllib
from pathlib import Path, PurePosixPath


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def digest_worktree(root: Path) -> str:
    digest = hashlib.sha256()
    package = root / "src/embedded_debug"
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix().encode()
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(relative + b"\0" + content + b"\0")
    return digest.hexdigest()


def digest_commit(revision: str) -> tuple[str, str]:
    files = git("ls-tree", "-r", "--name-only", revision, "--", "src/embedded_debug")
    digest = hashlib.sha256()
    for filename in sorted(line for line in files.splitlines() if line.endswith(".py")):
        relative = PurePosixPath(filename).relative_to("src/embedded_debug").as_posix()
        content = subprocess.run(
            ["git", "show", f"{revision}:{filename}"],
            capture_output=True,
            check=False,
        )
        if content.returncode:
            raise SystemExit(f"Could not read {filename} from pinned source {revision}")
        digest.update(relative.encode() + b"\0" + content.stdout + b"\0")
    raw_version = git("show", f"{revision}:pyproject.toml")
    version = tomllib.loads(raw_version)["project"]["version"]
    return digest.hexdigest(), version


root = Path(__file__).resolve().parents[3]
package_version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
skill = (root / "skills/embedded-debug/SKILL.md").read_text()
skill_match = re.search(r'(?m)^  version: "([^\"]+)"$', skill)
if not skill_match or skill_match.group(1) != package_version:
    raise SystemExit(
        f"Version mismatch: package={package_version}, skill="
        f"{skill_match.group(1) if skill_match else 'missing'}"
    )

launcher = (root / "skills/embedded-debug/scripts/run_edbg.py").read_text()
version_match = re.search(r'(?m)^PINNED_VERSION = "([^\"]+)"$', launcher)
if not version_match or version_match.group(1) != package_version:
    raise SystemExit(
        f"Version mismatch: package={package_version}, "
        f"launcher={version_match.group(1) if version_match else 'missing'}"
    )

source_match = re.search(
    r'(?m)^SOURCE = "git\+https://github\.com/tangsangsimida/embedded-debug-skill\.git@([0-9a-f]{40})"$',
    launcher,
)
fingerprint_match = re.search(r'(?m)^PINNED_SOURCE_DIGEST = "([0-9a-f]{64})"$', launcher)
if not source_match or not fingerprint_match:
    raise SystemExit(
        "Skill launcher must pin a full immutable commit SHA and 64-character source digest"
    )

source_revision = source_match.group(1)
source_digest, source_version = digest_commit(source_revision)
worktree_digest = digest_worktree(root)
if source_version != package_version:
    raise SystemExit(
        f"Pinned source version {source_version} does not match current package {package_version}"
    )
if source_digest != worktree_digest or source_digest != fingerprint_match.group(1):
    raise SystemExit(
        "Pinned source digest does not match current Python CLI source; update the launcher pin"
    )
print(
    f"Package/Skill version {package_version} and immutable CLI source "
    f"{source_revision[:12]} ({source_digest[:12]}) match"
)
