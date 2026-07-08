"""CLI integration tests for P1 acceptance criteria."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from lmm.cli import app
from lmm.config import ConfigStore
from lmm.state import StateStore


def test_p1_game_add_and_list(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    game_target: Path,
    kcd2_p1_game_args: list[str],
) -> None:
    result = runner.invoke(app, [*cli_args, *kcd2_p1_game_args])
    assert result.exit_code == 0, result.output
    config = ConfigStore(data_dir / "config.toml").load()
    assert "kcd2" in config.games
    assert config.games["kcd2"].nexus_domain == "kingdomcomedeliverance2"
    assert config.games["kcd2"].targets == [game_target]
    assert config.games["kcd2"].library_subpath == "KingdomComeDeliverance2/Mods"

    list_result = runner.invoke(app, [*cli_args, "--json", "game", "list"])
    assert list_result.exit_code == 0, list_result.output
    payload = json.loads(list_result.stdout)
    assert payload["kcd2"]["nexus_domain"] == "kingdomcomedeliverance2"


def test_p1_add_mod_and_list(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    kcd2_p1_profile: None,
) -> None:
    mod_source = data_dir / "incoming" / "easysharpening"
    mod_source.mkdir(parents=True)
    (mod_source / "mod.manifest").write_text("{}", encoding="utf-8")

    add_result = runner.invoke(
        app,
        [*cli_args, "add", str(mod_source), "--game", "kcd2"],
    )
    assert add_result.exit_code == 0, add_result.output

    state = StateStore(data_dir / "state.json").load()
    assert len(state.mods) == 1
    assert state.mods[0].name == "easysharpening"
    assert state.mods[0].game == "kcd2"

    list_result = runner.invoke(
        app,
        [*cli_args, "--json", "list", "--game", "kcd2"],
    )
    assert list_result.exit_code == 0, list_result.output
    mods = json.loads(list_result.stdout)
    assert len(mods) == 1
    assert mods[0]["name"] == "easysharpening"
    assert mods[0]["game"] == "kcd2"


def test_mod_list_accepts_positional_game(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile_minimal: None,
    data_dir: Path,
) -> None:
    mod_source = data_dir / "moddir"
    mod_source.mkdir()
    runner.invoke(app, [*cli_args, "add", str(mod_source), "--game", "kcd2"])

    list_result = runner.invoke(app, [*cli_args, "--json", "list", "kcd2"])
    assert list_result.exit_code == 0, list_result.output
    mods = json.loads(list_result.stdout)
    assert len(mods) == 1
    assert mods[0]["game"] == "kcd2"


def test_config_and_state_round_trip_via_cli(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    kcd2_profile_minimal: None,
) -> None:
    mod_source = data_dir / "moddir"
    mod_source.mkdir()
    runner.invoke(app, [*cli_args, "add", str(mod_source), "--game", "kcd2"])

    config = ConfigStore(data_dir / "config.toml").load()
    state = StateStore(data_dir / "state.json").load()
    ConfigStore(data_dir / "config.toml").save(config)
    StateStore(data_dir / "state.json").save(state)

    reloaded_config = ConfigStore(data_dir / "config.toml").load()
    reloaded_state = StateStore(data_dir / "state.json").load()
    assert reloaded_config.games["kcd2"].nexus_domain == "kcd"
    assert len(reloaded_state.mods) == 1
    assert reloaded_state.mods[0].name == "moddir"
