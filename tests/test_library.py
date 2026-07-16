"""Tests for library import and listing."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from lmm.archive import DOWNLOAD_DIRNAME
from lmm.config import Config, add_game_profile
from lmm.library import (
    ImportAction,
    LibraryError,
    discover_download_files,
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


def _make_mod_zip(path: Path, mod_name: str, *, manifest: str = "test") -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{mod_name}/mod.manifest", manifest)
        zf.writestr(f"{mod_name}/Data/mod.pak", "pak")


def test_import_mod_from_zip_creates_package(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "Easy Sharpening-68-1-1.zip"
    _make_mod_zip(archive, "easysharpening")
    state = State()
    updated_state, record, action = import_mod(
        config_with_game,
        state,
        archive,
        game_id="kcd2",
    )
    assert action == ImportAction.EXTRACTED
    expected = (
        config_with_game.library_root
        / "KingdomComeDeliverance2/Mods"
        / "easysharpening"
    )
    assert record.source_path == expected
    assert record.download_path == expected / DOWNLOAD_DIRNAME / archive.name
    assert (expected / "mod.manifest").read_text(encoding="utf-8") == "test"
    assert record.download_path.is_file()
    assert len(updated_state.mods) == 1


def test_import_mod_registers_existing_package(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    package = (
        config_with_game.library_root / "KingdomComeDeliverance2/Mods" / "nointro"
    )
    (package / DOWNLOAD_DIRNAME).mkdir(parents=True)
    (package / DOWNLOAD_DIRNAME / "No Intro.zip").write_bytes(b"zip")
    (package / "mod.manifest").write_text("test", encoding="utf-8")
    state = State()
    updated_state, record, action = import_mod(
        config_with_game,
        state,
        package,
        game_id="kcd2",
        name="nointro",
    )
    assert action == ImportAction.REGISTERED
    assert record.source_path == package.resolve()
    assert record.download_path == (
        package / DOWNLOAD_DIRNAME / "No Intro.zip"
    ).resolve()
    assert len(updated_state.mods) == 1


def test_import_mod_rejects_plain_directory(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    source = tmp_path / "external" / "easysharpening"
    source.mkdir(parents=True)
    (source / "mod.manifest").write_text("test", encoding="utf-8")
    with pytest.raises(LibraryError, match="Import the original Nexus download file"):
        import_mod(config_with_game, State(), source, game_id="kcd2")


def test_import_mod_unknown_game(config_with_game: Config, tmp_path: Path) -> None:
    source = tmp_path / "mod.zip"
    _make_mod_zip(source, "mod")
    with pytest.raises(LibraryError, match="Unknown game"):
        import_mod(config_with_game, State(), source, game_id="missing")


def test_list_mods_filters_by_game(config_with_game: Config, tmp_path: Path) -> None:
    archive_a = tmp_path / "mod-a.zip"
    archive_b = tmp_path / "mod-b.zip"
    _make_mod_zip(archive_a, "mod-a")
    _make_mod_zip(archive_b, "mod-b")
    state, _, _ = import_mod(config_with_game, State(), archive_a, game_id="kcd2")
    other = add_game_profile(
        config_with_game,
        "other",
        nexus_domain="othergame",
        targets=[tmp_path / "other"],
    )
    state, _, _ = import_mod(other, state, archive_b, game_id="other")
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
    archive = tmp_path / "external" / "movable.zip"
    archive.parent.mkdir(parents=True)
    _make_mod_zip(archive, "movable")
    _, record, action = import_mod(
        config_with_game,
        State(),
        archive,
        game_id="kcd2",
        copy=False,
    )
    assert action == ImportAction.EXTRACTED
    assert not archive.exists()
    assert record.source_path.exists()


def test_import_mod_destination_exists_raises(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "external" / "dup.zip"
    archive.parent.mkdir(parents=True)
    _make_mod_zip(archive, "dup")
    existing = config_with_game.library_root / "KingdomComeDeliverance2/Mods" / "dup"
    existing.mkdir(parents=True)
    with pytest.raises(LibraryError, match="Destination already exists"):
        import_mod(config_with_game, State(), archive, game_id="kcd2", name="dup")


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
    archive_a = tmp_path / "mod-a.zip"
    archive_b = tmp_path / "mod-b.zip"
    _make_mod_zip(archive_a, "mod-a")
    _make_mod_zip(archive_b, "mod-b")
    state, _, _ = import_mod(config_with_game, State(), archive_a, game_id="kcd2")
    other = add_game_profile(
        config_with_game,
        "other",
        nexus_domain="othergame",
        targets=[tmp_path / "other"],
    )
    state, _, _ = import_mod(other, state, archive_b, game_id="other")
    all_mods = list_mods(state, game_id=None)
    assert len(all_mods) == 2
    assert [mod.game for mod in all_mods] == ["kcd2", "other"]


def _downloads_dir(tmp_path: Path, *mod_names: str) -> Path:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    for mod_name in mod_names:
        _make_mod_zip(downloads / f"{mod_name}.zip", mod_name)
    return downloads


def test_discover_download_files(tmp_path: Path) -> None:
    downloads = _downloads_dir(tmp_path, "moda")
    (downloads / "readme.txt").write_text("skip", encoding="utf-8")
    assert discover_download_files(downloads) == [downloads / "moda.zip"]


def test_import_mods_from_directory_imports_archives(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    downloads = _downloads_dir(tmp_path, "moda", "modb")
    updated, results, failures, skips = import_mods_from_directory(
        config_with_game,
        State(),
        downloads,
        "kcd2",
    )
    assert failures == []
    assert skips == []
    assert len(results) == 2
    assert {item.record.name for item in results} == {"moda", "modb"}
    assert len(updated.mods) == 2


def test_import_mods_from_directory_skips_dirs_and_unsupported(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    _make_mod_zip(downloads / "moda.zip", "moda")
    (downloads / "extracted").mkdir()
    (downloads / "readme.txt").write_text("skip", encoding="utf-8")
    (downloads / ".hidden").mkdir()
    _, results, failures, skips = import_mods_from_directory(
        config_with_game,
        State(),
        downloads,
        "kcd2",
    )
    assert failures == []
    assert len(results) == 1
    assert results[0].record.name == "moda"
    assert len(skips) == 3
    assert {item.reason for item in skips} == {
        "not_a_download_file",
        "unsupported_file",
        "hidden",
    }


def test_import_mods_from_directory_skips_already_registered(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    downloads = _downloads_dir(tmp_path, "moda", "modb")
    state, _, _ = import_mod(
        config_with_game,
        State(),
        downloads / "moda.zip",
        game_id="kcd2",
    )
    updated, results, failures, skips = import_mods_from_directory(
        config_with_game,
        state,
        downloads,
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
    downloads = _downloads_dir(tmp_path, "moda", "modb")
    existing = config_with_game.library_root / "KingdomComeDeliverance2/Mods" / "moda"
    existing.mkdir(parents=True)
    updated, results, failures, skips = import_mods_from_directory(
        config_with_game,
        State(),
        downloads,
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
    downloads = _downloads_dir(tmp_path, "moda", "modb")
    state = State()
    updated, results, failures, skips = import_mods_from_directory(
        config_with_game,
        state,
        downloads,
        "kcd2",
        dry_run=True,
    )
    assert updated is state
    assert len(results) == 2
    assert failures == []
    assert not (
        config_with_game.library_root / "KingdomComeDeliverance2/Mods/moda"
    ).exists()
    assert (downloads / "moda.zip").exists()


def test_import_mods_from_directory_move(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    downloads = _downloads_dir(tmp_path, "moda")
    updated, results, _, _ = import_mods_from_directory(
        config_with_game,
        State(),
        downloads,
        "kcd2",
        copy=False,
    )
    assert results[0].action == ImportAction.EXTRACTED
    assert not (downloads / "moda.zip").exists()
    assert updated.mods[0].source_path.exists()


def test_import_loose_pak(tmp_path: Path, config_with_game: Config) -> None:
    pak = tmp_path / "mod.pak"
    pak.write_bytes(b"pak-content")
    _, record, action = import_mod(
        config_with_game,
        State(),
        pak,
        game_id="kcd2",
        name="loosemod",
    )
    assert action == ImportAction.EXTRACTED
    assert (record.source_path / "mod.pak").read_bytes() == b"pak-content"
    assert record.download_path == record.source_path / DOWNLOAD_DIRNAME / "mod.pak"
