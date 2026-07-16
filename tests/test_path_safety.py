"""Tests for path validation and confinement."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from lmm.config import Config, add_game_profile
from lmm.library import LibraryError, import_mod
from lmm.paths import (
    PathValidationError,
    path_within_root,
    resolve_under_root,
    validate_path_segment,
    validate_relative_subpath,
)
from lmm.state import State


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ("..", "mod name"),
        ("../escape", "mod name"),
        ("", "segment"),
        (".", "segment"),
    ],
)
def test_validate_path_segment_rejects_invalid(value: str, field: str) -> None:
    with pytest.raises(PathValidationError):
        validate_path_segment(value, field=field)


@pytest.mark.parametrize(
    "subpath",
    ["", "/etc/passwd", "../escape"],
)
def test_validate_relative_subpath_rejects_invalid(subpath: str) -> None:
    with pytest.raises(PathValidationError):
        validate_relative_subpath(subpath, field="library_subpath")


def test_path_within_root_positive(tmp_path: Path) -> None:
    root = tmp_path / "library"
    child = root / "KCD2" / "Mods" / "moda"
    child.mkdir(parents=True)
    assert path_within_root(child, root) is True


def test_path_within_root_negative(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    sibling = tmp_path / "outside"
    sibling.mkdir()
    assert path_within_root(sibling, root) is False


def test_resolve_under_root_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    with pytest.raises(PathValidationError):
        resolve_under_root(root, "..", "escape")


def test_add_game_profile_rejects_bad_game_id(tmp_path: Path) -> None:
    config = Config(library_root=tmp_path / "library")
    with pytest.raises(ValueError, match="game id"):
        add_game_profile(
            config,
            "../bad",
            nexus_domain="game",
            targets=[tmp_path / "target"],
        )


def test_add_game_profile_rejects_bad_library_subpath(tmp_path: Path) -> None:
    config = Config(library_root=tmp_path / "library")
    with pytest.raises(ValueError, match="library_subpath"):
        add_game_profile(
            config,
            "kcd2",
            nexus_domain="game",
            targets=[tmp_path / "target"],
            library_subpath="../escape",
        )


def test_import_mod_rejects_traversal_name(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    config = add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="game",
        targets=[tmp_path / "target"],
    )
    archive = tmp_path / "external-mod.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mod/file.txt", "x")
    with pytest.raises(LibraryError, match="mod name"):
        import_mod(
            config,
            State(),
            archive,
            game_id="kcd2",
            name="../../../escape",
        )


def test_import_mod_rejects_wrong_game_in_library(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    config = add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kcd",
        targets=[tmp_path / "kcd-target"],
        library_subpath="KingdomComeDeliverance2/Mods",
    )
    config = add_game_profile(
        config,
        "oblivion",
        nexus_domain="oblivion",
        targets=[tmp_path / "oblivion-target"],
        library_subpath="Oblivion/Mods",
    )
    kcd_mod = library_root / "KingdomComeDeliverance2/Mods" / "easysharpening"
    (kcd_mod / "download").mkdir(parents=True)
    (kcd_mod / "download" / "mod.zip").write_bytes(b"zip")
    (kcd_mod / "mod.manifest").write_text("x", encoding="utf-8")
    with pytest.raises(LibraryError, match="not under this game's directory"):
        import_mod(config, State(), kcd_mod, game_id="oblivion")
