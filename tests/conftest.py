"""Shared pytest fixtures."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lmm.config import Config, ConfigStore, add_game_profile
from lmm.library import import_mod
from lmm.state import State, StateStore


def make_mod_zip(
    path: Path,
    mod_name: str,
    *,
    inner_file: str = "file.txt",
    content: str = "x",
) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{mod_name}/{inner_file}", content)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    state_path = tmp_path / "state.json"
    library_root = tmp_path / "library"
    library_root.mkdir()
    config = Config(library_root=library_root)
    ConfigStore(config_path).save(config)
    StateStore(state_path).save(State())
    return tmp_path


@pytest.fixture
def cli_args(data_dir: Path) -> list[str]:
    return [
        "--config",
        str(data_dir / "config.toml"),
        "--state",
        str(data_dir / "state.json"),
    ]


@pytest.fixture
def game_target(tmp_path: Path) -> Path:
    target = tmp_path / "game" / "Mods"
    target.mkdir(parents=True)
    return target


def setup_kcd2_profile(
    data_dir: Path,
    game_target: Path,
    *,
    nexus_domain: str = "kcd",
    library_subpath: str | None = "KCD2/Mods",
) -> None:
    config_store = ConfigStore(data_dir / "config.toml")
    config = config_store.load()
    kwargs: dict[str, object] = {
        "nexus_domain": nexus_domain,
        "targets": [game_target],
    }
    if library_subpath is not None:
        kwargs["library_subpath"] = library_subpath
    updated = add_game_profile(config, "kcd2", **kwargs)
    config_store.save(updated)


def setup_kcd2_mod(
    data_dir: Path,
    *,
    mod_name: str = "mod",
    source_name: str | None = None,
) -> Path:
    config_store = ConfigStore(data_dir / "config.toml")
    state_store = StateStore(data_dir / "state.json")
    config = config_store.load()
    state = state_store.load()
    name = source_name or mod_name
    archive = data_dir / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{mod_name}/a.txt", "a")
    updated_state, record, _ = import_mod(
        config,
        state,
        archive,
        game_id="kcd2",
        name=mod_name if source_name else None,
    )
    state_store.save(updated_state)
    return record.source_path


@pytest.fixture
def kcd2_game_args(game_target: Path) -> list[str]:
    return [
        "game",
        "add",
        "kcd2",
        "--domain",
        "kcd",
        "--target",
        str(game_target),
        "--library-subpath",
        "KCD2/Mods",
    ]


@pytest.fixture
def kcd2_game_args_minimal(game_target: Path) -> list[str]:
    return [
        "game",
        "add",
        "kcd2",
        "--domain",
        "kcd",
        "--target",
        str(game_target),
    ]


@pytest.fixture
def kcd2_p1_game_args(game_target: Path) -> list[str]:
    return [
        "game",
        "add",
        "kcd2",
        "--domain",
        "kingdomcomedeliverance2",
        "--target",
        str(game_target),
        "--library-subpath",
        "KingdomComeDeliverance2/Mods",
    ]


@pytest.fixture
def kcd2_profile(data_dir: Path, game_target: Path) -> None:
    setup_kcd2_profile(data_dir, game_target)


@pytest.fixture
def kcd2_profile_minimal(data_dir: Path, game_target: Path) -> None:
    setup_kcd2_profile(data_dir, game_target, library_subpath=None)


@pytest.fixture
def kcd2_p1_profile(data_dir: Path, game_target: Path) -> None:
    setup_kcd2_profile(
        data_dir,
        game_target,
        nexus_domain="kingdomcomedeliverance2",
        library_subpath="KingdomComeDeliverance2/Mods",
    )


@pytest.fixture
def kcd2_with_mod(kcd2_profile: None, data_dir: Path) -> Path:
    return setup_kcd2_mod(data_dir)


@pytest.fixture
def kcd2_with_mod_minimal(kcd2_profile_minimal: None, data_dir: Path) -> Path:
    return setup_kcd2_mod(data_dir)
