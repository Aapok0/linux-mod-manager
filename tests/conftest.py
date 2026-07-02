"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

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
