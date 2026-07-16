"""Tests for in-place mod package updates."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from lmm.archive import parse_nexus_download_filename
from lmm.config import Config, add_game_profile
from lmm.deploy import deploy_game, deploy_mod
from lmm.library import (
    LibraryError,
    UpdateAction,
    import_mod,
    refresh_mod_package,
    update_mod,
    update_mods_from_directory,
)
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


def _make_mod_zip(path: Path, mod_name: str, *, manifest: str = "test") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{mod_name}/mod.manifest", manifest)
        zf.writestr(f"{mod_name}/Data/mod.pak", "pak")


def test_parse_nexus_download_filename() -> None:
    assert parse_nexus_download_filename(Path("Easy Sharpening-68-1-2.zip")) == 68
    assert parse_nexus_download_filename(Path("moda.zip")) is None


def test_refresh_mod_package_preserves_nexus_id(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    archive_v1 = tmp_path / "Easy Sharpening-68-1-1.zip"
    _make_mod_zip(archive_v1, "easysharpening", manifest="v1")
    state, record, _ = import_mod(
        config_with_game,
        State(),
        archive_v1,
        game_id="kcd2",
    )
    record = record.model_copy(update={"nexus_mod_id": 68, "file_md5": None})
    state.mods[0] = record

    archive_v2 = tmp_path / "Easy Sharpening-68-1-2.zip"
    _make_mod_zip(archive_v2, "easysharpening", manifest="v2")

    updated, action = refresh_mod_package(record, archive_v2)
    assert action == UpdateAction.EXTRACTED
    assert updated.nexus_mod_id == 68
    assert updated.download_path.name == archive_v2.name
    assert (record.source_path / "mod.manifest").read_text(encoding="utf-8") == "v2"


def test_refresh_mod_package_already_current_raises(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "moda.zip"
    _make_mod_zip(archive, "moda")
    _, record, _ = import_mod(config_with_game, State(), archive, game_id="kcd2")
    with pytest.raises(LibraryError, match="identical"):
        refresh_mod_package(record, archive)


def test_update_mod_only_updates_flagged(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    _make_mod_zip(downloads / "moda.zip", "moda", manifest="v1")
    _make_mod_zip(downloads / "modb.zip", "modb", manifest="v1")

    state, _, _ = import_mod(
        config_with_game,
        State(),
        downloads / "moda.zip",
        game_id="kcd2",
    )
    state, _, _ = import_mod(
        config_with_game,
        state,
        downloads / "modb.zip",
        game_id="kcd2",
    )
    state.mods[0] = state.mods[0].model_copy(update={"update_available": True})
    state.mods[1] = state.mods[1].model_copy(update={"update_available": False})

    _make_mod_zip(downloads / "moda.zip", "moda", manifest="v2")
    _make_mod_zip(downloads / "modb.zip", "modb", manifest="v2")

    updated, results, failures, skips = update_mods_from_directory(
        config_with_game,
        state,
        downloads,
        "kcd2",
        only_updates=True,
    )
    assert failures == []
    assert len(results) == 1
    assert results[0].record.name == "moda"
    assert len(skips) == 1
    assert skips[0].reason == "not_flagged"
    assert (updated.mods[0].source_path / "mod.manifest").read_text(
        encoding="utf-8"
    ) == "v2"
    assert (updated.mods[1].source_path / "mod.manifest").read_text(
        encoding="utf-8"
    ) == "v1"


def test_update_mods_from_directory_bulk(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    _make_mod_zip(downloads / "moda.zip", "moda", manifest="v1")
    _make_mod_zip(downloads / "modb.zip", "modb", manifest="v1")
    state, _, _ = import_mod(
        config_with_game,
        State(),
        downloads / "moda.zip",
        game_id="kcd2",
    )
    state, _, _ = import_mod(
        config_with_game,
        state,
        downloads / "modb.zip",
        game_id="kcd2",
    )

    _make_mod_zip(downloads / "moda.zip", "moda", manifest="v2")
    _make_mod_zip(downloads / "modb.zip", "modb", manifest="v2")
    _make_mod_zip(downloads / "unknown.zip", "unknown")

    updated, results, failures, skips = update_mods_from_directory(
        config_with_game,
        state,
        downloads,
        "kcd2",
    )
    assert failures == []
    assert len(results) == 2
    assert len(skips) == 1
    assert skips[0].reason == "not_registered"
    assert len(updated.mods) == 2


def test_partial_folder_leaves_missing_mods_untouched(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    state = State()
    for name in ("moda", "modb", "modc"):
        _make_mod_zip(downloads / f"{name}.zip", name, manifest="v1")
        state, _, _ = import_mod(
            config_with_game,
            state,
            downloads / f"{name}.zip",
            game_id="kcd2",
        )

    batch = tmp_path / "batch"
    batch.mkdir()
    _make_mod_zip(batch / "moda.zip", "moda", manifest="v2")
    _make_mod_zip(batch / "modb.zip", "modb", manifest="v2")

    small = tmp_path / "small"
    small.mkdir()
    _make_mod_zip(small / "moda.zip", "moda", manifest="v2")
    _make_mod_zip(small / "modb.zip", "modb", manifest="v2")

    updated, results, failures, skips = update_mods_from_directory(
        config_with_game,
        state,
        small,
        "kcd2",
    )
    assert failures == []
    assert skips == []
    assert {item.record.name for item in results} == {"moda", "modb"}
    assert (updated.mods[2].source_path / "mod.manifest").read_text(
        encoding="utf-8"
    ) == "v1"


def test_update_loose_pak(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    pak_v1 = tmp_path / "mod.pak"
    pak_v1.write_bytes(b"v1")
    state, record, _ = import_mod(
        config_with_game,
        State(),
        pak_v1,
        game_id="kcd2",
        name="loosemod",
    )

    pak_v2 = tmp_path / "mod-new.pak"
    pak_v2.write_bytes(b"v2")
    updated, new_record, action = update_mod(
        config_with_game,
        state,
        record,
        pak_v2,
    )
    assert action == UpdateAction.EXTRACTED
    assert (new_record.source_path / "mod-new.pak").read_bytes() == b"v2"
    assert updated.mods[0].download_path.name == "mod-new.pak"


def test_update_redeploys_changed_files(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    game_target = config_with_game.games["kcd2"].targets[0]
    archive_v1 = tmp_path / "moda.zip"
    _make_mod_zip(archive_v1, "moda", manifest="v1")
    state, record, _ = import_mod(
        config_with_game,
        State(),
        archive_v1,
        game_id="kcd2",
    )
    state, _ = deploy_game(config_with_game, state, "kcd2")
    link_path = game_target / "mod.manifest"
    assert link_path.is_symlink()
    assert link_path.read_text(encoding="utf-8") == "v1"

    archive_v2 = tmp_path / "moda-v2.zip"
    _make_mod_zip(archive_v2, "moda", manifest="v2")
    state, updated_record, _ = update_mod(
        config_with_game,
        state,
        record,
        archive_v2,
    )
    state, outcome = deploy_mod(config_with_game, state, updated_record)
    assert outcome.links_created >= 1
    assert link_path.read_text(encoding="utf-8") == "v2"


def test_update_removes_stale_symlink_when_file_removed(
    config_with_game: Config,
    tmp_path: Path,
) -> None:
    game_target = config_with_game.games["kcd2"].targets[0]

    archive_v1 = tmp_path / "moda.zip"
    with zipfile.ZipFile(archive_v1, "w") as zf:
        zf.writestr("moda/mod.manifest", "v1")
        zf.writestr("moda/extra.txt", "keep")

    state, record, _ = import_mod(
        config_with_game,
        State(),
        archive_v1,
        game_id="kcd2",
    )
    state, _ = deploy_game(config_with_game, state, "kcd2")
    extra_link = game_target / "extra.txt"
    assert extra_link.is_symlink()

    archive_v2 = tmp_path / "moda-v2.zip"
    _make_mod_zip(archive_v2, "moda", manifest="v2")
    state, updated_record, _ = update_mod(
        config_with_game,
        state,
        record,
        archive_v2,
    )
    state, _ = deploy_mod(config_with_game, state, updated_record)
    assert not extra_link.exists()
    assert (game_target / "mod.manifest").read_text(encoding="utf-8") == "v2"
