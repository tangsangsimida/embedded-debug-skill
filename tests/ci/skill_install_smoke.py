"""Cross-platform end-to-end checks for Skills CLI installation and CLI startup."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any


def run(
    command: list[str], *, env: dict[str, str], cwd: Path, timeout: float = 300
) -> subprocess.CompletedProcess[str]:
    if os.name == "nt" and Path(command[0]).suffix.casefold() in {".cmd", ".bat"}:
        command = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(command)]
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def python_command(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def create_test_venv(python: str, destination: Path, *, env: dict[str, str], cwd: Path) -> None:
    create = subprocess.run(
        [python, "-m", "venv", str(destination)],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=60,
        check=False,
    )
    if create.returncode == 0:
        return
    shutil.rmtree(destination, ignore_errors=True)
    run(
        [python, "-m", "venv", "--without-pip", str(destination)], env=env, cwd=cwd, timeout=60
    )
    run(
        [
            python,
            "-m",
            "pip",
            "--python",
            str(python_command(destination)),
            "install",
            "pip",
        ],
        env=env,
        cwd=cwd,
    )


def edbg_command(search_path: str | None = None) -> str:
    executable = shutil.which("edbg.exe" if os.name == "nt" else "edbg", path=search_path)
    if executable is None:
        raise RuntimeError("Installed edbg entry point is not available on PATH")
    return executable


def run_edbg_script(python: Path, skill: Path, *args: str, env: dict[str, str]) -> str:
    return run(
        [str(python), str(skill / "scripts" / "run_edbg.py"), *args], env=env, cwd=Path.cwd()
    ).stdout


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    runner_temp = Path(os.environ["RUNNER_TEMP"])
    agent = os.environ["SKILL_AGENT"]
    home = runner_temp / "agent-home"
    home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"HOME": str(home), "DISABLE_TELEMETRY": "1"})
    if os.name == "nt":
        env["USERPROFILE"] = str(home)

    npx = shutil.which("npx")
    if npx is None:
        raise RuntimeError("Skills CLI test requires npx on PATH")
    event = env.get("GITHUB_EVENT_NAME", "")
    ref = env.get("GITHUB_REF", "")
    if event == "push" and ref in {"refs/heads/main", "refs/heads/master"}:
        source = "tangsangsimida/embedded-debug-skill"
    else:
        source = "."
    run(
        [
            npx,
            "--yes",
            "skills@1.7.6",
            "add",
            source,
            "--skill",
            "embedded-debug",
            "--agent",
            agent,
            "--copy",
            "-g",
            "-y",
        ],
        env=env,
        cwd=root,
    )

    skill_dir = (
        ".agents/skills/embedded-debug" if agent == "codex" else ".claude/skills/embedded-debug"
    )
    skill = home / skill_dir
    required = [
        "SKILL.md",
        "references/setup.md",
        "references/debugging.md",
        "references/hardfault.md",
        "references/threadx.md",
        "references/safety.md",
        "scripts/run_edbg.py",
        "scripts/check_version.py",
    ]
    for relative in required:
        if not (skill / relative).is_file():
            raise RuntimeError(f"Installed Skill is missing {relative}")
    for source_file in (root / "skills/embedded-debug").rglob("*"):
        if not source_file.is_file() or "__pycache__" in source_file.parts:
            continue
        installed = skill / source_file.relative_to(root / "skills/embedded-debug")
        if not installed.is_file() or installed.read_bytes() != source_file.read_bytes():
            relative = source_file.relative_to(root)
            raise RuntimeError(f"Installed Skill resource differs: {relative}")

    venv = runner_temp / "cli-runtime"
    create_test_venv(sys.executable, venv, env=env, cwd=root)
    venv_python = python_command(venv)
    run_edbg_script(venv_python, skill, "--install-python-cli", "--version", env=env)
    run_edbg_script(venv_python, skill, "--help", env=env)
    doctor_text = run_edbg_script(venv_python, skill, "--json", "doctor", env=env)
    doctor: dict[str, Any] = json.loads(doctor_text)
    if doctor.get("success") is not True or "debug_tools_ready" not in doctor.get("data", {}):
        raise RuntimeError("Installed CLI doctor response does not satisfy the JSON contract")
    environment_text = run_edbg_script(
        venv_python, skill, "--json", "environment", "inspect", env=env
    )
    environment: dict[str, Any] = json.loads(environment_text)
    if environment.get("success") is not True or "tools" not in environment.get("data", {}):
        raise RuntimeError("Installed CLI environment response does not satisfy the JSON contract")
    plan_text = run_edbg_script(
        venv_python, skill, "--json", "environment", "plan", env=env
    )
    plan: dict[str, Any] = json.loads(plan_text)
    if plan.get("success") is not True or not isinstance(plan.get("data", {}).get("actions"), list):
        raise RuntimeError("Installed CLI environment plan does not satisfy the JSON contract")

    run([sys.executable, "-m", "pip", "install", "-e", ".[dev]"], env=env, cwd=root)
    release_wheel = env.get("EDBG_RELEASE_WHEEL")
    release_dir = env.get("EDBG_RELEASE_WHEEL_DIR")
    if release_wheel or release_dir:
        if release_wheel:
            wheel = Path(release_wheel)
        else:
            candidates = sorted(Path(release_dir).glob("*.whl"))
            if len(candidates) != 1:
                raise RuntimeError(
                    f"Expected one release wheel in {release_dir}, found {len(candidates)}"
                )
            wheel = candidates[0]
        if not wheel.is_file():
            raise RuntimeError(f"Release wheel does not exist: {wheel}")
    else:
        run([sys.executable, "-m", "build"], env=env, cwd=root)
        wheels = sorted((root / "dist").glob("*.whl"))
        if not wheels:
            raise RuntimeError("Package build did not create a wheel")
        wheel = wheels[0]
    run(
        [sys.executable, "-m", "pip", "install", "--force-reinstall", str(wheel)],
        env=env,
        cwd=root,
    )
    scripts = sysconfig.get_path("scripts")
    env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
    command = edbg_command(env.get("PATH"))
    run([command, "--version"], env=env, cwd=root)
    run([command, "--help"], env=env, cwd=root)
    package_doctor = run([command, "doctor", "--json"], env=env, cwd=root)
    package_data = json.loads(package_doctor.stdout)
    if package_data.get("success") is not True or "cli_ready" not in package_data.get("data", {}):
        raise RuntimeError("Packaged CLI doctor response does not satisfy the JSON contract")
    package_environment = run([command, "environment", "inspect", "--json"], env=env, cwd=root)
    if json.loads(package_environment.stdout).get("success") is not True:
        raise RuntimeError("Packaged CLI environment inspection failed")
    package_plan = run([command, "environment", "plan", "--json"], env=env, cwd=root)
    if not isinstance(json.loads(package_plan.stdout).get("data", {}).get("actions"), list):
        raise RuntimeError("Packaged CLI environment plan failed")
    installed_build = json.loads(run([command, "--json", "build-info"], env=env, cwd=root).stdout)
    launcher_build = json.loads(
        run_edbg_script(venv_python, skill, "--json", "build-info", env=env)
    )
    if installed_build.get("data") != launcher_build.get("data"):
        raise RuntimeError("Installed Python wheel and Skill launcher source pins do not match")
    run([sys.executable, "-m", "pytest"], env=env, cwd=root)
    run(
        [
            sys.executable,
            "skills/embedded-debug/scripts/validate_skill.py",
            "skills/embedded-debug",
        ],
        env=env,
        cwd=root,
    )
    run([sys.executable, "skills/embedded-debug/scripts/check_version.py"], env=env, cwd=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
