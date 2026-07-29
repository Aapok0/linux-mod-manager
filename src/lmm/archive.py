"""Archive extraction and download-file detection for mod packages."""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

from lmm.paths import path_within_root

_NEXUS_DOWNLOAD_FILENAME = re.compile(
    r"-(?P<mod_id>\d+)-[\d.]+(?:-\d+)?$",
    re.IGNORECASE,
)

DOWNLOAD_DIRNAME = "download"

ARCHIVE_SUFFIXES = frozenset({".zip", ".7z", ".rar"})

LOOSE_DOWNLOAD_SUFFIXES = frozenset(
    {
        ".pak",
        ".ba2",
        ".mpmod",
        ".dll",
        ".esp",
        ".esm",
        ".bsa",
    }
)

_ZIP_SYMLINK_MASK = 0o170000
_ZIP_SYMLINK_TYPE = 0o120000


class ArchiveError(Exception):
    """Raised when archive extraction fails."""


def is_archive(path: Path) -> bool:
    return path.suffix.lower() in ARCHIVE_SUFFIXES


def is_loose_download(path: Path) -> bool:
    return path.suffix.lower() in LOOSE_DOWNLOAD_SUFFIXES


def is_download_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in ARCHIVE_SUFFIXES or suffix in LOOSE_DOWNLOAD_SUFFIXES


def parse_nexus_download_filename(path: Path) -> int | None:
    """Return Nexus mod id from a Vortex-style download filename, if present."""
    match = _NEXUS_DOWNLOAD_FILENAME.search(path.stem)
    if match is None:
        return None
    return int(match.group("mod_id"))


def peek_archive_root_name(archive: Path) -> str | None:
    """Return single top-level directory name inside an archive, if present."""
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        return _peek_zip_root_name(archive)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        _extract_with_7z(archive, staging)
        return _single_top_level_dir_name(staging)


def _peek_zip_root_name(archive: Path) -> str | None:
    with zipfile.ZipFile(archive) as zf:
        roots: set[str] = set()
        for name in zf.namelist():
            if not name or name.startswith("__MACOSX/"):
                continue
            parts = Path(name).parts
            if not parts:
                continue
            roots.add(parts[0])
        if len(roots) == 1:
            root = next(iter(roots))
            for name in zf.namelist():
                if name.startswith("__MACOSX/"):
                    continue
                rel = Path(name)
                if len(rel.parts) == 1 and rel.name and not name.endswith("/"):
                    return None
            return root
    return None


def _single_top_level_dir_name(staging: Path) -> str | None:
    entries = [entry for entry in staging.iterdir() if not entry.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0].name
    return None


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and (mode & _ZIP_SYMLINK_MASK) == _ZIP_SYMLINK_TYPE:
        return True
    # MS-DOS attribute bit for reparse/symlink is uncommon; create_system 3 = Unix.
    return False


def _safe_zip_member_path(name: str, dest: Path) -> Path:
    if not name or name.endswith("/"):
        # Directory entries are created implicitly when extracting files.
        return dest
    if "\x00" in name:
        msg = f"Unsafe archive member path (NUL): {name!r}"
        raise ArchiveError(msg)
    member = Path(name)
    if (
        member.is_absolute()
        or name.startswith(("/", "\\"))
        or name.startswith(("~/", "~\\"))
    ):
        msg = f"Unsafe archive member path (absolute): {name!r}"
        raise ArchiveError(msg)
    if ".." in member.parts:
        msg = f"Unsafe archive member path (parent segment): {name!r}"
        raise ArchiveError(msg)
    dest_root = dest.resolve()
    target = (dest / member).resolve()
    if not path_within_root(target, dest_root):
        msg = f"Unsafe archive member path (escapes destination): {name!r}"
        raise ArchiveError(msg)
    return dest / member


def _assert_extracted_tree_safe(root: Path) -> None:
    root_resolved = root.resolve()
    for path in root.rglob("*"):
        if path.is_symlink():
            msg = f"Archive extraction produced a symlink: {path}"
            raise ArchiveError(msg)
        if not path_within_root(path, root_resolved):
            msg = f"Archive extraction escaped destination: {path}"
            raise ArchiveError(msg)


def _extract_zip(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename
            if not name or name.startswith("__MACOSX/"):
                continue
            if _zip_member_is_symlink(info):
                msg = f"Refusing to extract symlink from archive: {name!r}"
                raise ArchiveError(msg)
            if name.endswith("/"):
                _safe_zip_member_path(name.rstrip("/") + "/.", dest)
                continue
            target = _safe_zip_member_path(name, dest)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            # Preserve executable bit when present (Unix).
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                target.chmod(target.stat().st_mode | (mode & 0o111))
    _assert_extracted_tree_safe(dest)


def _extract_with_7z(archive: Path, dest: Path) -> None:
    if shutil.which("7z") is None:
        msg = (
            f"Cannot extract {archive.name}: '7z' not found on PATH. "
            "Install p7zip (e.g. pacman -S p7zip / apt install p7zip-full)."
        )
        raise ArchiveError(msg)
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["7z", "x", str(archive), f"-o{dest}", "-y"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        msg = f"Failed to extract {archive.name} with 7z"
        if detail:
            msg = f"{msg}: {detail}"
        raise ArchiveError(msg)
    _assert_extracted_tree_safe(dest)


def _extract_to_staging(archive: Path, staging: Path) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        _extract_zip(archive, staging)
        return
    _extract_with_7z(archive, staging)


def _promote_extracted_root(staging: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    entries = [entry for entry in staging.iterdir() if not entry.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        source_root = entries[0]
    else:
        source_root = staging
    for item in source_root.iterdir():
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target, symlinks=False)
        else:
            shutil.copy2(item, target)


def extract_archive(archive: Path, dest: Path) -> None:
    """Extract archive into dest, stripping a single top-level directory."""
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        _extract_to_staging(archive, staging)
        _promote_extracted_root(staging, dest)
