"""Tests for library import and listing."""

from __future__ import annotations

from pathlib import Path

import pytest

from lmm.config import Config, add_game_profile
from lmm.library import (
    ImportAction,
    LibraryError,
    discover_mod_dirs,
    import_mod,
    import_mods_from_directory,
    list_mods,
    mod_is_deployed,
    resolve_mod_source,
)
from lmm.state import DeployedLink, ModRecord, State


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
    updated_state, record, action = import_mod(
        config_with_game,
        state,
        source,
        game_id="kcd2",
    )
    assert action == ImportAction.COPIED
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
    updated_state, record, action = import_mod(
        config_with_game,
        state,
        existing,
        game_id="kcd2",
        name="nointro",
    )
    assert action == ImportAction.REGISTERED
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
    state, _, _ = import_mod(config_with_game, State(), source, game_id="kcd2")
    other = add_game_profile(
        config_with_game,
        "other",
        nexus_domain="othergame",
        targets=[tmp_path / "other"],
    )
    source_b = tmp_path / "mod-b"
    source_b.mkdir()
    state, _, _ = import_mod(other, state, source_b, game_id="other")
    kcd2_mods = list_mods(state, "kcd2")
    assert len(kcd2_mods) == 1
    assert kcd2_mods[0].game == "kcd2"


def test_resolve_mod_source_bare_name(config_with_game: Config) -> None:
    mod_dir = (
        config_with_game.library_root / "KingdomComeDeliverance2/Mods/easysharpening"
    )
    mod_dir.mkdir(parents=True)
    resolved = resolve_mod_source(config_with_game, "kcd2", Path("easysharpening"))
    assert resolved == mod_dir.resolve()


def test_import_mod_move_removes_source(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    source = tmp_path / "external" / "movable"
    source.mkdir(parents=True)
    (source / "mod.manifest").write_text("test", encoding="utf-8")
    _, record, action = import_mod(
        config_with_game,
        State(),
        source,
        game_id="kcd2",
        copy=False,
    )
    assert action == ImportAction.MOVED
    assert not source.exists()
    assert record.source_path.exists()


def test_import_mod_destination_exists_raises(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    source = tmp_path / "external" / "dup"
    source.mkdir(parents=True)
    existing = config_with_game.library_root / "KingdomComeDeliverance2/Mods" / "dup"
    existing.mkdir(parents=True)
    with pytest.raises(LibraryError, match="Destination already exists"):
        import_mod(config_with_game, State(), source, game_id="kcd2", name="dup")


def test_mod_is_deployed() -> None:
    deployed = State(
        mods=[
            ModRecord(
                name="a",
                game="kcd2",
                source_path=Path("/tmp/a"),
                deployed_links=[
                    DeployedLink(link=Path("/game/a.txt"), source=Path("/tmp/a.txt"))
                ],
            )
        ]
    ).mods[0]
    not_deployed = State(
        mods=[ModRecord(name="b", game="kcd2", source_path=Path("/tmp/b"))]
    ).mods[0]
    assert mod_is_deployed(deployed) is True
    assert mod_is_deployed(not_deployed) is False


def test_list_mods_all_games_sorted(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    source_a = tmp_path / "mod-a"
    source_a.mkdir()
    state, _, _ = import_mod(config_with_game, State(), source_a, game_id="kcd2")
    other = add_game_profile(
        config_with_game,
        "other",
        nexus_domain="othergame",
        targets=[tmp_path / "other"],
    )
    source_b = tmp_path / "mod-b"
    source_b.mkdir()
    state, _, _ = import_mod(other, state, source_b, game_id="other")
    all_mods = list_mods(state, game_id=None)
    assert len(all_mods) == 2
    assert [mod.game for mod in all_mods] == ["kcd2", "other"]


def _staging_dir(tmp_path: Path, *mod_names: str) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir()
    for mod_name in mod_names:
        mod_dir = staging / mod_name
        mod_dir.mkdir()
        (mod_dir / "mod.txt").write_text(mod_name, encoding="utf-8")
    return staging


def test_discover_mod_dirs_excludes_hidden(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "moda").mkdir()
    (staging / ".hidden").mkdir()
    assert discover_mod_dirs(staging) == [staging / "moda"]


def test_import_mods_from_directory_imports_children(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    staging = _staging_dir(tmp_path, "moda", "modb")
    updated, results, failures, skips = import_mods_from_directory(
        config_with_game,
        State(),
        staging,
        "kcd2",
    )
    assert failures == []
    assert skips == []
    assert len(results) == 2
    assert {item.record.name for item in results} == {"moda", "modb"}
    assert len(updated.mods) == 2


def test_import_mods_from_directory_skips_files_and_hidden(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "moda").mkdir()
    (staging / "archive.zip").write_text("zip", encoding="utf-8")
    (staging / ".hidden").mkdir()
    _, results, failures, skips = import_mods_from_directory(
        config_with_game,
        State(),
        staging,
        "kcd2",
    )
    assert failures == []
    assert len(results) == 1
    assert results[0].record.name == "moda"
    assert len(skips) == 2
    assert {item.reason for item in skips} == {"not_a_directory", "hidden"}


def test_import_mods_from_directory_skips_already_registered(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    staging = _staging_dir(tmp_path, "moda", "modb")
    state, _, _ = import_mod(
        config_with_game,
        State(),
        staging / "moda",
        game_id="kcd2",
    )
    updated, results, failures, skips = import_mods_from_directory(
        config_with_game,
        state,
        staging,
        "kcd2",
    )
    assert failures == []
    assert len(results) == 1
    assert results[0].record.name == "modb"
    assert len(skips) == 1
    assert skips[0].reason == "already_registered"
    assert len(updated.mods) == 2


def test_import_mods_from_directory_continues_after_failure(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    staging = _staging_dir(tmp_path, "moda", "modb")
    existing = config_with_game.library_root / "KingdomComeDeliverance2/Mods" / "moda"
    existing.mkdir(parents=True)
    updated, results, failures, skips = import_mods_from_directory(
        config_with_game,
        State(),
        staging,
        "kcd2",
    )
    assert skips == []
    assert len(failures) == 1
    assert failures[0].name == "moda"
    assert len(results) == 1
    assert results[0].record.name == "modb"
    assert len(updated.mods) == 1


def test_import_mods_from_directory_dry_run(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    staging = _staging_dir(tmp_path, "moda", "modb")
    state = State()
    updated, results, failures, skips = import_mods_from_directory(
        config_with_game,
        state,
        staging,
        "kcd2",
        dry_run=True,
    )
    assert updated is state
    assert len(results) == 2
    assert failures == []
    assert not (
        config_with_game.library_root / "KingdomComeDeliverance2/Mods/moda"
    ).exists()
    assert staging / "moda" in discover_mod_dirs(staging)


def test_import_mods_from_directory_move(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    staging = _staging_dir(tmp_path, "moda")
    updated, results, _, _ = import_mods_from_directory(
        config_with_game,
        State(),
        staging,
        "kcd2",
        copy=False,
    )
    assert results[0].action == ImportAction.MOVED
    assert not (staging / "moda").exists()
    assert updated.mods[0].source_path.exists()
