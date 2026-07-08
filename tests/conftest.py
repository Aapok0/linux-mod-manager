"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lmm.cli import app
from lmm.config import Config, ConfigStore
from lmm.state import State, StateStore


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
def kcd2_profile(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_game_args: list[str],
) -> None:
    result = runner.invoke(app, [*cli_args, *kcd2_game_args])
    assert result.exit_code == 0, result.output


@pytest.fixture
def kcd2_profile_minimal(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_game_args_minimal: list[str],
) -> None:
    result = runner.invoke(app, [*cli_args, *kcd2_game_args_minimal])
    assert result.exit_code == 0, result.output


@pytest.fixture
def kcd2_p1_profile(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_p1_game_args: list[str],
) -> None:
    result = runner.invoke(app, [*cli_args, *kcd2_p1_game_args])
    assert result.exit_code == 0, result.output


@pytest.fixture
def kcd2_with_mod(
    kcd2_profile: None,
    runner: CliRunner,
    cli_args: list[str],
    data_dir: Path,
) -> Path:
    mod_source = data_dir / "mod"
    mod_source.mkdir()
    (mod_source / "a.txt").write_text("a", encoding="utf-8")
    result = runner.invoke(app, [*cli_args, "add", str(mod_source), "--game", "kcd2"])
    assert result.exit_code == 0, result.output
    return mod_source


@pytest.fixture
def kcd2_with_mod_minimal(
    kcd2_profile_minimal: None,
    runner: CliRunner,
    cli_args: list[str],
    data_dir: Path,
) -> Path:
    mod_source = data_dir / "mod"
    mod_source.mkdir()
    (mod_source / "a.txt").write_text("a", encoding="utf-8")
    result = runner.invoke(app, [*cli_args, "add", str(mod_source), "--game", "kcd2"])
    assert result.exit_code == 0, result.output
    return mod_source
