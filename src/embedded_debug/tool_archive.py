"""Explicitly provision reviewed tool archives without changing host toolchains."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import IO, Any

from .doctor import inspect_gdb_mi, inspect_tool
from .platforms import get_platform

_MAX_ARCHIVE = 2 * 1024**3
_MAX_UNPACKED = 4 * 1024**3
_MAX_FILES = 100_000


class _HTTPSOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("Archive redirects must remain on HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _relative(name: str) -> Path:
    if "\\" in name or "\x00" in name or ":" in name:
        raise ValueError(f"Unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(p in {".", ".."} for p in path.parts):
        raise ValueError(f"Unsafe archive path: {name!r}")
    for part in path.parts:
        if part != part.rstrip(" .") or re.fullmatch(
            r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", part
        ):
            raise ValueError(f"Unsafe archive path: {name!r}")
    return Path(*path.parts)


def _copy_bounded(source: IO[bytes], target: IO[bytes], maximum: int) -> None:
    total = 0
    while chunk := source.read(1024 * 1024):
        total += len(chunk)
        if total > maximum:
            raise ValueError("Archive exceeds the permitted size")
        target.write(chunk)


def _fetch(source: str, target: Path) -> None:
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme in {"https", "http"} or "://" in source:
        if parsed.scheme != "https":
            raise ValueError("Remote archive URLs must use HTTPS")
        opener = urllib.request.build_opener(_HTTPSOnlyRedirect())
        with opener.open(source, timeout=60) as response, target.open("wb") as out:
            if urllib.parse.urlsplit(response.geturl()).scheme != "https":
                raise ValueError("Archive redirects must remain on HTTPS")
            _copy_bounded(response, out, _MAX_ARCHIVE)
    else:
        with Path(source).expanduser().open("rb") as inp, target.open("wb") as out:
            _copy_bounded(inp, out, _MAX_ARCHIVE)


def _unpack(archive: Path, root: Path) -> None:
    """Extract regular files/directories only; links and special entries fail closed."""
    seen: set[Path] = set()
    count = 0
    total = 0

    def write(name: str, size: int, mode: int, inp: IO[bytes] | None) -> None:
        nonlocal count, total
        if size < 0:
            raise ValueError("Archive contains a negative file size")
        path = _relative(name)
        if path in seen:
            raise ValueError(f"Duplicate archive path: {name!r}")
        seen.add(path)
        count += 1
        total += size
        if count > _MAX_FILES or total > _MAX_UNPACKED:
            raise ValueError("Archive exceeds extraction limits")
        destination = root / path
        if inp is None:
            destination.mkdir(parents=True, exist_ok=True)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as out:
            _copy_bounded(inp, out, size)
        if mode & 0o111:
            destination.chmod(destination.stat().st_mode | 0o111)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise ValueError(f"Archive contains a link or special file: {item.filename}")
                if item.is_dir():
                    write(item.filename, 0, mode, None)
                else:
                    with package.open(item) as inp:
                        write(item.filename, item.file_size, mode, inp)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive, "r:*") as tar_package:
            for tar_item in tar_package:
                if tar_item.isdir():
                    if tar_item.name in {".", "./"}:
                        continue
                    write(tar_item.name, 0, tar_item.mode, None)
                elif tar_item.isfile():
                    tar_input = tar_package.extractfile(tar_item)
                    if tar_input is None:
                        raise ValueError(f"Cannot read archive entry: {tar_item.name}")
                    with tar_input:
                        write(tar_item.name, tar_item.size, tar_item.mode, tar_input)
                else:
                    raise ValueError(f"Archive contains a link or special file: {tar_item.name}")
    else:
        raise ValueError("Expected a ZIP or tar archive")


def install_tool_archive(
    source: str, sha256: str, executable: str, kind: str, *, authorized: bool = False
) -> dict[str, Any]:
    if not authorized:
        raise PermissionError("Installing an isolated tool archive requires explicit authorization")
    if kind not in {"gdb", "openocd"} or not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
        raise ValueError("Provide kind gdb/openocd and a full SHA-256 digest")
    relative_executable = _relative(executable)
    digest = sha256.lower()
    parent = get_platform().cache_dir() / "tool-archives"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / digest
    with get_platform().file_lock(parent / f"{digest}.lock"):
        if destination.exists():
            raise FileExistsError("This archive cache already exists; inspect it before reuse")
        with tempfile.TemporaryDirectory(prefix=".staging-", dir=parent) as temp:
            staging = Path(temp)
            archive = staging / "download"
            _fetch(source, archive)
            with archive.open("rb") as inp:
                actual = hashlib.file_digest(inp, "sha256").hexdigest()
            if actual != digest:
                raise ValueError("Tool archive SHA-256 does not match the reviewed digest")
            content = staging / "content"
            content.mkdir()
            try:
                _unpack(archive, content)
            except (zipfile.BadZipFile, tarfile.TarError) as exc:
                raise ValueError(f"Invalid tool archive: {exc}") from exc
            binary = content / relative_executable
            if not binary.is_file():
                raise ValueError("The requested executable was not found in the archive")
            checked = inspect_tool(kind, str(binary), required=True, timeout=10)
            if checked["status"] != "available":
                raise RuntimeError(
                    f"Installed executable failed version check: {checked.get('detail')}"
                )
            mi = inspect_gdb_mi(str(binary), timeout=10) if kind == "gdb" else None
            if kind == "gdb" and mi != "supported":
                raise RuntimeError("Installed GDB did not pass its MI2 check")
            manifest = {
                "sha256": digest,
                "kind": kind,
                "executable": executable,
                "version": checked["version"],
                "mi2": mi,
                "source_host": urllib.parse.urlsplit(source).hostname or "local",
            }
            if (content / ".edbg-tool.json").exists():
                raise ValueError("Archive contains a reserved manifest path")
            (content / ".edbg-tool.json").write_text(json.dumps(manifest), encoding="utf-8")
            content.rename(destination)
    return {**manifest, "path": str(destination / relative_executable)}


def remove_tool_archive(sha256: str, *, authorized: bool = False) -> dict[str, str]:
    if not authorized:
        raise PermissionError("Removing an isolated tool archive requires explicit authorization")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
        raise ValueError("A full SHA-256 digest is required")
    parent = get_platform().cache_dir() / "tool-archives"
    destination = parent / sha256.lower()
    with get_platform().file_lock(parent / f"{sha256.lower()}.lock"):
        if not (destination / ".edbg-tool.json").is_file():
            raise ValueError("No managed tool archive exists for this digest")
        from .session_store import read_session, session_names

        for name in session_names():
            session = read_session(name) or {}
            for key in ("gdb_executable", "openocd_executable"):
                value = session.get(key)
                if isinstance(value, str) and Path(value).resolve().is_relative_to(
                    destination.resolve()
                ):
                    raise RuntimeError(
                        f"Tool archive is referenced by session {name!r}; stop or inspect it first"
                    )
        shutil.rmtree(destination)
    return {"sha256": sha256.lower(), "removed": str(destination)}
