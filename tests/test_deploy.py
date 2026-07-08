"""Tests for deploy engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from lmm.config import Config, add_game_profile
from lmm.deploy import (
    DeployError,
    build_link_plan,
    deploy_game,
    resolve_deploy_target,
    undeploy_game,
)
from lmm.library import import_mod
from lmm.state import ModRecord, State


@pytest.fixture
def deploy_setup(tmp_path: Path) -> tuple[Config, Path, Path]:
    library_root = tmp_path / "library"
    library_root.mkdir()
    game_target = tmp_path / "game" / "Mods"
    game_target.mkdir(parents=True)
    alt_target = tmp_path / "game" / "Binaries"
    alt_target.mkdir(parents=True)
    config = add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kcd",
        targets=[game_target, alt_target],
        library_subpath="KCD2/Mods",
    )
    return config, game_target, alt_target


def _add_mod(
    config: Config,
    state: State,
    tmp_path: Path,
    *,
    name: str,
    target: int | str | None = None,
) -> tuple[Config, State]:
    source = tmp_path / "incoming" / name
    source.mkdir(parents=True)
    (source / "data.txt").write_text("mod", encoding="utf-8")
    state, _ = import_mod(config, state, source, game_id="kcd2", target=target)
    return config, state


def test_resolve_deploy_target_default(deploy_setup: tuple[Config, Path, Path]) -> None:
    config, game_target, _ = deploy_setup
    mod = ModRecord(
        name="foo",
        game="kcd2",
        source_path=Path("/tmp/foo"),
    )
    assert resolve_deploy_target(config, mod) == game_target.resolve()


def test_resolve_deploy_target_index(deploy_setup: tuple[Config, Path, Path]) -> None:
    config, _, alt_target = deploy_setup
    mod = ModRecord(
        name="foo",
        game="kcd2",
        source_path=Path("/tmp/foo"),
        target=1,
    )
    assert resolve_deploy_target(config, mod) == alt_target.resolve()


def test_deploy_creates_symlinks(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    updated, outcome = deploy_game(config, state, "kcd2")
    link = game_target / "data.txt"
    assert link.is_symlink()
    assert link.resolve() == (config.library_root / "KCD2/Mods/moda/data.txt").resolve()
    assert outcome.links_created == 1
    assert len(updated.mods[0].deployed_links) == 1


def test_deploy_default_target_for_most_mods(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, alt_target = deploy_setup
    state = State()
    _, state = _add_mod(config, state, tmp_path, name="moda")
    _, state = _add_mod(config, state, tmp_path, name="modb", target=1)
    deploy_game(config, state, "kcd2")
    assert (game_target / "data.txt").is_symlink()
    assert (alt_target / "data.txt").is_symlink()


def test_deploy_idempotent(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, _, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    first, _ = deploy_game(config, state, "kcd2")
    second, outcome = deploy_game(config, first, "kcd2")
    assert outcome.links_created == 0
    assert outcome.links_skipped == 1
    assert len(second.mods[0].deployed_links) == 1


def test_deploy_skips_foreign_file(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    foreign = game_target / "data.txt"
    foreign.write_text("real game file", encoding="utf-8")
    _, outcome = deploy_game(config, state, "kcd2")
    assert outcome.links_created == 0
    assert len(outcome.conflicts) == 1
    assert foreign.read_text(encoding="utf-8") == "real game file"


def test_undeploy_removes_only_recorded_links(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    deployed, _ = deploy_game(config, state, "kcd2")
    link = game_target / "data.txt"
    assert link.is_symlink()
    updated, outcome = undeploy_game(config, deployed, "kcd2")
    assert outcome.links_removed == 1
    assert not link.exists()
    assert len(updated.mods[0].deployed_links) == 0


def test_dry_run_deploy_does_not_write(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    updated, outcome = deploy_game(config, state, "kcd2", dry_run=True)
    assert outcome.links_created == 1
    assert updated is state
    assert not (game_target / "data.txt").exists()


def test_build_link_plan_skips_disabled_mods(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, _, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    state.mods[0].enabled = False
    plan = build_link_plan(config, state, "kcd2")
    assert plan == []


def test_resolve_deploy_target_invalid_index(
    deploy_setup: tuple[Config, Path, Path],
) -> None:
    config, _, _ = deploy_setup
    mod = ModRecord(
        name="foo",
        game="kcd2",
        source_path=Path("/tmp/foo"),
        target=9,
    )
    with pytest.raises(DeployError, match="out of range"):
        resolve_deploy_target(config, mod)


def test_deploy_removes_disabled_mod_links(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    deployed, _ = deploy_game(config, state, "kcd2")
    link = game_target / "data.txt"
    assert link.is_symlink()

    disabled = deployed.model_copy(deep=True)
    disabled.mods[0] = disabled.mods[0].model_copy(update={"enabled": False})
    updated, outcome = deploy_game(config, disabled, "kcd2")
    assert outcome.links_removed == 1
    assert not link.exists()
    assert len(updated.mods[0].deployed_links) == 0
    assert updated.mods[0].enabled is False


def test_deploy_does_not_adopt_foreign_symlink(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    source = config.library_root / "KCD2/Mods/moda/data.txt"
    link = game_target / "data.txt"
    link.symlink_to(source)
    _, outcome = deploy_game(config, state, "kcd2")
    assert outcome.links_created == 0
    assert len(outcome.conflicts) == 1
    assert link.is_symlink()
    assert len(state.mods[0].deployed_links) == 0


def test_created_dirs_lifecycle(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    source = tmp_path / "incoming" / "nested"
    nested_dir = source / "sub"
    nested_dir.mkdir(parents=True)
    (nested_dir / "data.txt").write_text("mod", encoding="utf-8")
    state, _ = import_mod(config, State(), source, game_id="kcd2", name="nested")
    deployed, _ = deploy_game(config, state, "kcd2")
    created = game_target / "sub"
    assert created.is_dir()
    assert created in deployed.mods[0].created_dirs
    updated, _ = undeploy_game(config, deployed, "kcd2")
    assert not (game_target / "sub" / "data.txt").exists()
    assert not created.exists()
    assert updated.mods[0].created_dirs == []


def test_undeploy_skips_changed_symlink(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    deployed, _ = deploy_game(config, state, "kcd2")
    link = game_target / "data.txt"
    other = tmp_path / "other.txt"
    other.write_text("other", encoding="utf-8")
    link.unlink()
    link.symlink_to(other)
    updated, outcome = undeploy_game(config, deployed, "kcd2")
    assert len(outcome.warnings) == 1
    assert link.exists()
    assert len(updated.mods[0].deployed_links) == 1
