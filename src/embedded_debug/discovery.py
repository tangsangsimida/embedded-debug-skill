"""Conservative discovery of an existing project's debug/build setup."""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

import psutil

from .openocd.manager import OpenOCDManager

_SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox"}


def _same_file_entry(left: Path, right: Path) -> bool:
    try:
        if os.path.samefile(left, right):
            return True
    except OSError:
        pass
    try:
        left_stat, right_stat = left.stat(), right.stat()
    except OSError:
        return False
    return left_stat.st_dev == right_stat.st_dev and left_stat.st_ino == right_stat.st_ino


def _expand_workspace(value: str, root: Path) -> Path:
    value = value.replace("${workspaceFolder}", str(root)).replace("${workspaceRoot}", str(root))
    return (root / value).resolve() if not Path(value).is_absolute() else Path(value)


def _strip_jsonc(text: str) -> str:
    """Remove comments and trailing commas without changing quoted strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
        elif char == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
        elif char == "/" and following == "*":
            index += 2
            while index + 1 < len(text) and text[index : index + 2] != "*/":
                index += 1
            index = min(index + 2, len(text))
        else:
            output.append(char)
            index += 1
    return re.sub(r",\s*([}\]])", r"\1", "".join(output))


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(_strip_jsonc(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _elf_candidates(root: Path, max_depth: int = 5) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for current, dirs, files in os.walk(root):
        path = Path(current)
        depth = len(path.relative_to(root).parts)
        dirs[:] = [name for name in dirs if name not in _SKIP and depth < max_depth]
        for filename in files:
            if filename.lower().endswith(".elf"):
                found.append({"path": str(path / filename), "source": "filesystem"})
    return sorted(found, key=lambda item: ("build" not in item["path"].lower(), item["path"]))[:50]


def _launch_configs(root: Path) -> list[dict[str, Any]]:
    path = root / ".vscode" / "launch.json"
    document = _load_json(path)
    if not document:
        return []
    candidates = []
    for config in document.get("configurations", []):
        if not isinstance(config, dict):
            continue
        candidate: dict[str, Any] = {"name": config.get("name"), "source": str(path)}
        for key in ("executable", "program"):
            if isinstance(config.get(key), str):
                elf = _expand_workspace(config[key], root)
                if elf.suffix.lower() == ".elf":
                    candidate["elf"] = str(elf)
        for key in (
            "servertype",
            "serverpath",
            "gdbTarget",
            "gdbPort",
            "device",
            "gdbPath",
            "gdbExecutable",
        ):
            if key in config:
                candidate[key] = config[key]
        for key in ("configFiles", "serverArgs", "openOCDLaunchCommands"):
            if isinstance(config.get(key), list):
                candidate[key] = config[key]
        if config.get("preLaunchTask"):
            candidate["preLaunchTask"] = config["preLaunchTask"]
        candidates.append(candidate)
    return candidates


def _task_commands(root: Path) -> list[dict[str, Any]]:
    path = root / ".vscode" / "tasks.json"
    document = _load_json(path)
    if not document:
        return []
    tasks: list[dict[str, Any]] = []
    for task in document.get("tasks", []):
        if not isinstance(task, dict):
            continue
        command, args = task.get("command"), task.get("args", [])
        if isinstance(command, str):
            argv = [command, *(str(arg) for arg in args)] if isinstance(args, list) else [command]
            tasks.append({"label": task.get("label"), "argv": argv, "source": str(path)})
    return tasks


def _script_openocd_commands(root: Path) -> list[dict[str, Any]]:
    """Extract literal OpenOCD invocations from common existing build files."""
    paths = [root / "CMakeLists.txt", root / "Makefile", root / "makefile"]
    paths.extend(sorted(root.glob("*.mk")))
    paths.extend(sorted(root.glob("*.sh")))
    paths.extend(sorted((root / "scripts").glob("*.sh")))
    paths.extend(sorted((root / "debug").glob("*.sh")))
    candidates: list[dict[str, Any]] = []
    unique_paths: list[Path] = []
    for path in paths:
        # Windows filesystems commonly resolve Makefile and makefile to the
        # same entry. Path.samefile() is the strongest check when available;
        # normcase also handles aliases when filesystem identity is unavailable
        # (for example, a mocked path or a transient permission error).
        normalized = os.path.normcase(str(path.resolve(strict=False)))
        normalized_alias = any(
            os.path.normcase(str(previous.resolve(strict=False))) == normalized
            for previous in unique_paths
        )
        if normalized_alias:
            continue
        if any(_same_file_entry(path, previous) for previous in unique_paths):
            continue
        unique_paths.append(path)
    for path in unique_paths:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, 1):
            if "openocd" not in line.lower():
                continue
            # A CMake COMMAND keyword or Make recipe marker is syntax, not an
            # executable argument. Shell operators are not executed by discovery.
            source_line = line.strip().lstrip("@+").strip()
            try:
                tokens = shlex.split(source_line, comments=True, posix=True)
            except ValueError:
                continue
            index = next(
                (
                    i
                    for i, token in enumerate(tokens)
                    if Path(token.lstrip("@+")).name.lower() == "openocd"
                    or token.strip("${}").lower() in {"openocd", "openocd_executable"}
                ),
                None,
            )
            if index is None:
                continue
            executable = tokens[index].lstrip("@+")
            candidates.append(
                {
                    "source": f"{path}:{line_number}",
                    "config_files": [],
                    "commands": [],
                    "server_args": tokens[index + 1 :],
                    "server_path": executable if "/" in executable else None,
                    "requires_review": any(
                        "$" in token or "${" in token for token in tokens[index + 1 :]
                    ),
                }
            )
    return candidates


def _build_candidate(root: Path, tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for task in tasks:
        label = str(task.get("label", "")).lower()
        if any(word in label for word in ("build", "compile")):
            return {"argv": task["argv"], "source": task["source"], "label": task["label"]}
    if (root / "CMakeLists.txt").is_file():
        for build_dir in ("build", "cmake-build-debug", "cmake-build-release"):
            if (root / build_dir).is_dir():
                return {"argv": ["cmake", "--build", build_dir], "source": "CMakeLists.txt"}
        return {"argv": None, "source": "CMakeLists.txt", "status": "configure_required"}
    if (root / "Makefile").is_file() or (root / "makefile").is_file():
        return {"argv": ["make"], "source": "Makefile"}
    if (root / "platformio.ini").is_file():
        return {"argv": ["pio", "run"], "source": "platformio.ini"}
    return None


def _openocd_listening_ports() -> set[int]:
    """Find loopback listeners owned by an OpenOCD process, when observable.

    These are candidates only: the GDB/MI handshake remains the authority that
    confirms a candidate is actually a usable GDB server.
    """
    ports: set[int] = set()
    try:
        for process in psutil.process_iter(["name"]):
            try:
                name = str(process.info.get("name", "")).casefold()
                if name not in {"openocd", "openocd.exe"}:
                    continue
                for connection in process.net_connections(kind="tcp"):
                    if connection.status != psutil.CONN_LISTEN or not connection.laddr:
                        continue
                    if connection.laddr.ip in {"127.0.0.1", "::1", "0.0.0.0", "::"}:
                        ports.add(int(connection.laddr.port))
            except (psutil.Error, OSError):
                continue
    except (psutil.Error, OSError):
        return ports
    return ports


def _flash_candidate(root: Path, tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for task in tasks:
        if any(
            word in str(task.get("label", "")).lower() for word in ("flash", "program", "upload")
        ):
            return task
    makefiles = [root / "Makefile", root / "makefile", *sorted(root.glob("*.mk"))]
    target_pattern = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?![=])")
    for path in makefiles:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = target_pattern.match(line)
            if match and match.group(1).lower() in {"flash", "program", "upload", "load"}:
                return {
                    "label": match.group(1),
                    "argv": ["make", match.group(1)],
                    "source": str(path),
                }
    cmake_targets: list[str] = []
    custom_target = re.compile(r"add_custom_target\s*\(\s*([A-Za-z0-9_.-]+)", re.IGNORECASE)
    for path in (root / "CMakeLists.txt", *sorted(root.glob("**/CMakeLists.txt"))):
        if not path.is_file() or any(part in _SKIP for part in path.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        cmake_targets.extend(
            match.group(1)
            for match in custom_target.finditer(source)
            if match.group(1).lower() in {"flash", "program", "upload", "load"}
        )
    if cmake_targets:
        build_dir = next(
            (
                name
                for name in ("build", "cmake-build-debug", "cmake-build-release")
                if (root / name).is_dir()
            ),
            None,
        )
        if build_dir:
            return {
                "label": cmake_targets[0],
                "argv": ["cmake", "--build", build_dir, "--target", cmake_targets[0]],
                "source": "CMakeLists.txt",
            }
    return None


def discover_project(
    root: Path | None = None, ports: tuple[int, ...] = (3333, 3334, 2331)
) -> dict[str, Any]:
    project = (root or Path.cwd()).resolve()
    launches = _launch_configs(project)
    tasks = _task_commands(project)
    elfs = _elf_candidates(project)
    for config in launches:
        if config.get("elf"):
            elfs.insert(0, {"path": config["elf"], "source": config["source"]})
    openocd_launches = [
        item
        for item in launches
        if str(item.get("servertype", "")).lower() == "openocd"
        or "configFiles" in item
        or "gdbTarget" in item
        or "serverArgs" in item
        or "serverpath" in item
    ]
    config_candidates = [
        {
            "source": item["source"],
            "config_files": item.get("configFiles", []),
            "gdb_target": item.get("gdbTarget"),
            "gdb_port": item.get("gdbPort"),
            "server_path": item.get("serverpath"),
            "gdb_executable": item.get("gdbPath") or item.get("gdbExecutable"),
            "commands": item.get("openOCDLaunchCommands", []),
            "server_args": item.get("serverArgs", []),
        }
        for item in openocd_launches
    ]
    for task in tasks:
        argv = task["argv"]
        if "openocd" not in Path(argv[0]).name.lower():
            continue
        config_files: list[str] = []
        commands: list[str] = []
        index = 1
        while index < len(argv):
            if argv[index] == "-f" and index + 1 < len(argv):
                config_files.append(argv[index + 1])
                index += 2
            elif argv[index] == "-c" and index + 1 < len(argv):
                commands.append(argv[index + 1])
                index += 2
            else:
                index += 1
        config_candidates.append(
            {
                "source": task["source"],
                # Preserve the exact invocation. Re-splitting and then passing
                # both these fields and the original argv duplicates OpenOCD args.
                "config_files": [],
                "commands": [],
                "server_args": argv[1:],
                "gdb_port": next(
                    (
                        int(match.group(1))
                        for command in commands
                        if (match := re.search(r"\bgdb_port\s+(\d+)\b", command))
                    ),
                    None,
                ),
                "task": task.get("label"),
            }
        )
    config_candidates.extend(_script_openocd_commands(project))
    candidate_ports = set(ports) | _openocd_listening_ports()
    for candidate in config_candidates:
        port = candidate.get("gdb_port")
        if isinstance(port, int) and not isinstance(port, bool):
            candidate_ports.add(port)
        elif isinstance(port, str) and port.isdigit():
            candidate_ports.add(int(port))
        target = candidate.get("gdb_target")
        if isinstance(target, str) and ":" in target:
            host, raw_port = target.rsplit(":", 1)
            if host in {"localhost", "127.0.0.1", "::1"} and raw_port.isdigit():
                candidate_ports.add(int(raw_port))
    active = [
        {"host": "127.0.0.1", "port": port, "status": "reachable"}
        for port in sorted(candidate_ports)
        if OpenOCDManager.port_open("127.0.0.1", port)
    ]
    return {
        "project_root": str(project),
        "build": _build_candidate(project, tasks),
        "flash": _flash_candidate(project, tasks),
        "build_tasks": tasks,
        "debug_configurations": launches,
        "openocd_candidates": config_candidates,
        "active_gdb_servers": active,
        "elf_candidates": elfs[:50],
        "config_status": (
            "existing_server"
            if active
            else "project_configuration_found"
            if config_candidates
            else "needs_review"
        ),
    }


def openocd_launch_options(discovery: dict[str, Any]) -> dict[str, Any]:
    """Return only unambiguous options from an IDE OpenOCD launch configuration."""
    candidates = discovery["openocd_candidates"]
    if len(candidates) != 1:
        return {}
    candidate = candidates[0]
    if candidate.get("requires_review"):
        return {}
    server_args = list(candidate.get("server_args", []))
    # Command based project configurations are already complete argv vectors.
    # IDE launch settings can combine serverArgs with separate config fields.
    command_based = bool(server_args) and candidate.get("task") is not None
    config_files = list(candidate.get("config_files", []))
    commands = list(candidate.get("commands", []))
    if server_args and not command_based:
        # Some IDEs put the complete OpenOCD argv in serverArgs. Extract -f/-c
        # entries once, leaving unrelated flags to be forwarded verbatim.
        remaining: list[str] = []
        index = 0
        while index < len(server_args):
            if server_args[index] in {"-f", "-c"} and index + 1 < len(server_args):
                flag, value = server_args[index], server_args[index + 1]
                (config_files if flag == "-f" else commands).append(value)
                index += 2
            else:
                remaining.append(server_args[index])
                index += 1
        server_args = remaining
    options: dict[str, Any] = {
        "config_files": [] if command_based else config_files,
        "commands": [] if command_based else commands,
        "server_path": candidate.get("server_path"),
    }
    target = candidate.get("gdb_target")
    if isinstance(target, str) and ":" in target:
        host, port = target.rsplit(":", 1)
        if host in {"localhost", "127.0.0.1", "::1"} and port.isdigit():
            options["host"], options["gdb_port"] = host, int(port)
    elif isinstance(candidate.get("gdb_port"), int):
        options["gdb_port"] = candidate["gdb_port"]
    elif str(candidate.get("gdb_port", "")).isdigit():
        options["gdb_port"] = int(candidate["gdb_port"])
    if candidate.get("gdb_executable"):
        options["gdb_executable"] = candidate["gdb_executable"]
    options["server_args"] = server_args
    if "gdb_port" not in options:
        command_values = [*options["commands"]]
        server_args = options["server_args"]
        command_values.extend(
            server_args[index + 1] for index, value in enumerate(server_args[:-1]) if value == "-c"
        )
        for command in command_values:
            match = re.search(r"\bgdb_port\s+(\d+)\b", str(command))
            if match:
                options["gdb_port"] = int(match.group(1))
                break
    return options
