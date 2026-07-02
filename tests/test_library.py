"""Tests for library import and listing."""

from __future__ import annotations

from pathlib import Path

import pytest

from lmm.config import Config, add_game_profile
from lmm.library import LibraryError, import_mod, list_mods
from lmm.state import State


@pytest.fixture
def config_with_game(tmp_path: Path) -> Config:
    library_root = tmp_path / "library"
    library_root.mkdir()
    return add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kingdomcomedeliverance2",
        targets=[tmp_path / "game" / "Mods"],
        library_subpath="KingdomComeDeliverance2/Mods",
    )


def test_import_mod_copies_external_dir(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    source = tmp_path / "external" / "easysharpening"
    source.mkdir(parents=True)
    (source / "mod.manifest").write_text("test", encoding="utf-8")
    state = State()
    _, updated_state, record = import_mod(
        config_with_game,
        state,
        source,
        game_id="kcd2",
    )
    expected = (
        config_with_game.library_root
        / "KingdomComeDeliverance2/Mods"
        / "easysharpening"
    )
    assert record.source_path == expected
    assert expected.exists()
    assert (expected / "mod.manifest").read_text(encoding="utf-8") == "test"
    assert len(updated_state.mods) == 1


def test_import_mod_registers_existing_library_path(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    existing = (
        config_with_game.library_root / "KingdomComeDeliverance2/Mods" / "nointro"
    )
    existing.mkdir(parents=True)
    state = State()
    _, updated_state, record = import_mod(
        config_with_game,
        state,
        existing,
        game_id="kcd2",
        name="nointro",
    )
    assert record.source_path == existing.resolve()
    assert len(updated_state.mods) == 1


def test_import_mod_unknown_game(config_with_game: Config, tmp_path: Path) -> None:
    source = tmp_path / "mod"
    source.mkdir()
    with pytest.raises(LibraryError, match="Unknown game"):
        import_mod(config_with_game, State(), source, game_id="missing")


def test_list_mods_filters_by_game(config_with_game: Config, tmp_path: Path) -> None:
    source = tmp_path / "mod-a"
    source.mkdir()
    _, state, _ = import_mod(config_with_game, State(), source, game_id="kcd2")
    other = add_game_profile(
        config_with_game,
        "other",
        nexus_domain="othergame",
        targets=[tmp_path / "other"],
    )
    source_b = tmp_path / "mod-b"
    source_b.mkdir()
    _, state, _ = import_mod(other, state, source_b, game_id="other")
    kcd2_mods = list_mods(state, "kcd2")
    assert len(kcd2_mods) == 1
    assert kcd2_mods[0].game == "kcd2"
