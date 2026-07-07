"""Tests for game deploy target management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lmm.cli import app
from lmm.config import (
    Config,
    ConfigStore,
    add_game_profile,
    add_game_target,
    remove_game_target,
)
from lmm.state import (
    ModRecord,
    State,
    StateStore,
    add_mod_record,
    adjust_mod_targets_after_remove,
    mods_referencing_target_index,
)


def _game_config(tmp_path: Path, *targets: Path) -> Config:
    return add_game_profile(
        Config(library_root=tmp_path / "library"),
        "kcd2",
        nexus_domain="kingdomcomedeliverance2",
        targets=list(targets),
    )


def test_add_game_target_appends(tmp_path: Path) -> None:
    primary = tmp_path / "game" / "Mods"
    secondary = tmp_path / "game" / "Data"
    config = _game_config(tmp_path, primary)
    updated = add_game_target(config, "kcd2", secondary)
    assert updated.games["kcd2"].targets == [primary, secondary]


def test_add_game_target_rejects_duplicate(tmp_path: Path) -> None:
    target = tmp_path / "game" / "Mods"
    config = _game_config(tmp_path, target)
    with pytest.raises(ValueError, match="already configured"):
        add_game_target(config, "kcd2", target)


def test_add_game_target_rejects_unknown_game(tmp_path: Path) -> None:
    config = Config()
    with pytest.raises(ValueError, match="Unknown game profile"):
        add_game_target(config, "missing", tmp_path / "Mods")


def test_remove_game_target_rejects_index_zero(tmp_path: Path) -> None:
    primary = tmp_path / "game" / "Mods"
    secondary = tmp_path / "game" / "Data"
    config = _game_config(tmp_path, primary, secondary)
    with pytest.raises(ValueError, match="primary deploy target"):
        remove_game_target(config, "kcd2", 0)


def test_remove_game_target_rejects_last_target(tmp_path: Path) -> None:
    primary = tmp_path / "game" / "Mods"
    config = _game_config(tmp_path, primary)
    with pytest.raises(ValueError, match="at least one deploy target"):
        remove_game_target(config, "kcd2", 1)


def test_remove_game_target_rejects_out_of_range(tmp_path: Path) -> None:
    primary = tmp_path / "game" / "Mods"
    secondary = tmp_path / "game" / "Data"
    config = _game_config(tmp_path, primary, secondary)
    with pytest.raises(ValueError, match="out of range"):
        remove_game_target(config, "kcd2", 2)


def test_adjust_mod_targets_after_remove(tmp_path: Path) -> None:
    state = State()
    state = add_mod_record(
        state,
        ModRecord(
            name="default-mod",
            game="kcd2",
            source_path=tmp_path / "library" / "kcd2" / "default-mod",
        ),
    )
    state = add_mod_record(
        state,
        ModRecord(
            name="alt-mod",
            game="kcd2",
            source_path=tmp_path / "library" / "kcd2" / "alt-mod",
            target=2,
        ),
    )
    updated = adjust_mod_targets_after_remove(state, "kcd2", 1)
    assert updated.mods[1].target == 1


def test_mods_referencing_target_index() -> None:
    state = State(
        mods=[
            ModRecord(name="a", game="kcd2", source_path=Path("/a"), target=1),
            ModRecord(name="b", game="kcd2", source_path=Path("/b"), target=2),
            ModRecord(name="c", game="other", source_path=Path("/c"), target=1),
        ]
    )
    refs = mods_referencing_target_index(state, "kcd2", 1)
    assert [mod.name for mod in refs] == ["a"]


def test_cli_game_target_add_list_remove(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    game_target: Path,
) -> None:
    alt_target = game_target.parent / "Data"
    runner.invoke(
        app,
        [
            *cli_args,
            "game",
            "add",
            "kcd2",
            "--domain",
            "kingdomcomedeliverance2",
            "--target",
            str(game_target),
        ],
    )
    add_result = runner.invoke(
        app,
        [
            *cli_args,
            "game",
            "target",
            "add",
            "kcd2",
            "--target",
            str(alt_target),
        ],
    )
    assert add_result.exit_code == 0, add_result.output
    config = ConfigStore(data_dir / "config.toml").load()
    assert config.games["kcd2"].targets == [game_target, alt_target]

    list_result = runner.invoke(
        app,
        [*cli_args, "--json", "game", "target", "list", "kcd2"],
    )
    assert list_result.exit_code == 0, list_result.output
    payload = json.loads(list_result.stdout)
    assert payload["targets"] == [
        {"index": 0, "path": str(game_target)},
        {"index": 1, "path": str(alt_target)},
    ]

    remove_result = runner.invoke(
        app,
        [*cli_args, "game", "target", "remove", "kcd2", "--index", "1"],
    )
    assert remove_result.exit_code == 0, remove_result.output
    config = ConfigStore(data_dir / "config.toml").load()
    assert config.games["kcd2"].targets == [game_target]


def test_cli_game_target_remove_blocked_when_mod_references_index(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    game_target: Path,
) -> None:
    alt_target = game_target.parent / "Data"
    runner.invoke(
        app,
        [
            *cli_args,
            "game",
            "add",
            "kcd2",
            "--domain",
            "kingdomcomedeliverance2",
            "--target",
            str(game_target),
            "--target",
            str(alt_target),
        ],
    )
    mod_source = data_dir / "incoming" / "alt-mod"
    mod_source.mkdir(parents=True)
    runner.invoke(
        app,
        [
            *cli_args,
            "add",
            str(mod_source),
            "--game",
            "kcd2",
            "--target-index",
            "1",
        ],
    )

    remove_result = runner.invoke(
        app,
        [*cli_args, "game", "target", "remove", "kcd2", "--index", "1"],
    )
    assert remove_result.exit_code != 0
    assert "alt-mod" in remove_result.output


def test_cli_game_target_remove_decrements_higher_indices(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    game_target: Path,
) -> None:
    alt_target = game_target.parent / "Data"
    third_target = game_target.parent / "Binaries"
    runner.invoke(
        app,
        [
            *cli_args,
            "game",
            "add",
            "kcd2",
            "--domain",
            "kingdomcomedeliverance2",
            "--target",
            str(game_target),
            "--target",
            str(alt_target),
            "--target",
            str(third_target),
        ],
    )
    mod_source = data_dir / "incoming" / "binary-mod"
    mod_source.mkdir(parents=True)
    runner.invoke(
        app,
        [
            *cli_args,
            "add",
            str(mod_source),
            "--game",
            "kcd2",
            "--target-index",
            "2",
        ],
    )

    remove_result = runner.invoke(
        app,
        [*cli_args, "game", "target", "remove", "kcd2", "--index", "1"],
    )
    assert remove_result.exit_code == 0, remove_result.output

    state = StateStore(data_dir / "state.json").load()
    assert state.mods[0].target == 1
