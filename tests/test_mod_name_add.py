"""Tests for bare mod name resolution on add."""

from __future__ import annotations

from pathlib import Path

from lmm.config import Config, add_game_profile
from lmm.library import import_mod, resolve_mod_source
from lmm.state import State


def test_resolve_mod_source_bare_name(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    config = add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kcd",
        targets=[tmp_path / "game" / "Mods"],
        library_subpath="KCD2/Mods",
    )
    mod_dir = library_root / "KCD2/Mods/easysharpening"
    mod_dir.mkdir(parents=True)
    resolved = resolve_mod_source(config, "kcd2", Path("easysharpening"))
    assert resolved == mod_dir.resolve()


def test_add_by_bare_mod_name(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    config = add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kcd",
        targets=[tmp_path / "game" / "Mods"],
        library_subpath="KCD2/Mods",
    )
    mod_dir = library_root / "KCD2/Mods/nointro"
    mod_dir.mkdir(parents=True)
    state, record = import_mod(
        config,
        State(),
        resolve_mod_source(config, "kcd2", Path("nointro")),
        game_id="kcd2",
    )
    assert record.source_path == mod_dir.resolve()
    assert len(state.mods) == 1
