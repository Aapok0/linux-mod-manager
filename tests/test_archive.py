"""Tests for archive extraction helpers."""

from __future__ import annotations

import zipfile
from pathlib import Path

from lmm.archive import (
    DOWNLOAD_DIRNAME,
    extract_archive,
    is_download_file,
    peek_archive_root_name,
)


def _make_zip(path: Path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def test_is_download_file_accepts_archives_and_loose_pak() -> None:
    assert is_download_file(Path("mod.zip")) is True
    assert is_download_file(Path("mod.pak")) is True
    assert is_download_file(Path("readme.txt")) is False


def test_peek_archive_root_name_single_dir(tmp_path: Path) -> None:
    archive = tmp_path / "mod.zip"
    _make_zip(
        archive,
        {
            "easysharpening/mod.manifest": "x",
            "easysharpening/Data/mod.pak": "pak",
        },
    )
    assert peek_archive_root_name(archive) == "easysharpening"


def test_extract_archive_strips_single_root_dir(tmp_path: Path) -> None:
    archive = tmp_path / "mod.zip"
    _make_zip(
        archive,
        {
            "easysharpening/mod.manifest": "manifest",
            "easysharpening/Data/mod.pak": "pak",
        },
    )
    dest = tmp_path / "package"
    extract_archive(archive, dest)
    assert (dest / "mod.manifest").read_text(encoding="utf-8") == "manifest"
    assert (dest / "Data" / "mod.pak").read_bytes() == b"pak"


def test_extract_archive_keeps_flat_layout(tmp_path: Path) -> None:
    archive = tmp_path / "flat.zip"
    _make_zip(
        archive,
        {
            "mod.manifest": "manifest",
            "Data/mod.pak": "pak",
        },
    )
    dest = tmp_path / "package"
    extract_archive(archive, dest)
    assert (dest / "mod.manifest").exists()
    assert (dest / "Data" / "mod.pak").exists()


def test_download_dirname_constant() -> None:
    assert DOWNLOAD_DIRNAME == "download"
