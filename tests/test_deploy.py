"""Tests for deploy engine."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lmm.config import Config, GameProfile, add_game_profile
from lmm.deploy import (
    DeployError,
    build_link_plan,
    deploy_game,
    remove_mod,
    resolve_deploy_target,
    resolve_link_path,
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
    state, _, _ = import_mod(config, state, source, game_id="kcd2", target=target)
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
    state, _, _ = import_mod(config, State(), source, game_id="kcd2", name="nested")
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


def test_resolve_deploy_target_string_override(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, _, _ = deploy_setup
    custom = tmp_path / "custom" / "deploy"
    custom.mkdir(parents=True)
    mod = ModRecord(
        name="foo",
        game="kcd2",
        source_path=Path("/tmp/foo"),
        target=str(custom),
    )
    assert resolve_deploy_target(config, mod) == custom.resolve()


def test_deploy_unknown_game_raises(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, _, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    with pytest.raises(DeployError, match="Unknown game profile"):
        deploy_game(config, state, "bogus")


def test_build_link_plan_missing_source_raises(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, _, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    source = state.mods[0].source_path
    shutil.rmtree(source)
    with pytest.raises(DeployError, match="not a directory"):
        build_link_plan(config, state, "kcd2")


def test_remove_mod_dry_run_keeps_links_and_state(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    deployed, _ = deploy_game(config, state, "kcd2")
    mod = deployed.mods[0]
    link = game_target / "data.txt"
    assert link.is_symlink()

    updated, outcome = remove_mod(config, deployed, mod, dry_run=True)
    assert outcome.links_removed == 1
    assert outcome.dry_run is True
    assert link.is_symlink()
    assert len(updated.mods) == 1
    assert len(updated.mods[0].deployed_links) == 1


def test_remove_mod_delete_files_under_library(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    _, state = _add_mod(config, State(), tmp_path, name="moda")
    deployed, _ = deploy_game(config, state, "kcd2")
    mod = deployed.mods[0]
    source = mod.source_path
    assert source.exists()

    updated, outcome = remove_mod(config, deployed, mod, delete_files=True)
    assert outcome.deleted_files is True
    assert outcome.links_removed == 1
    assert not (game_target / "data.txt").exists()
    assert not source.exists()
    assert updated.mods == []


def test_remove_mod_delete_files_refused_outside_library(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, _, _ = deploy_setup
    external = tmp_path / "external" / "moda"
    external.mkdir(parents=True)
    (external / "data.txt").write_text("mod", encoding="utf-8")
    mod = ModRecord(
        name="moda",
        game="kcd2",
        source_path=external,
    )
    state = State(mods=[mod])

    with pytest.raises(
        DeployError, match="Refusing to delete files outside library_root"
    ):
        remove_mod(config, state, mod, delete_files=True)
    assert external.exists()
    assert len(state.mods) == 1


def test_deploy_string_target_override(
    deploy_setup: tuple[Config, Path, Path],
    tmp_path: Path,
) -> None:
    config, game_target, _ = deploy_setup
    custom = tmp_path / "custom" / "deploy"
    custom.mkdir(parents=True)
    _, state = _add_mod(config, State(), tmp_path, name="moda", target=str(custom))
    deploy_game(config, state, "kcd2")
    assert (custom / "data.txt").is_symlink()
    assert not (game_target / "data.txt").exists()


def _mod_subdir_config(
    tmp_path: Path,
) -> tuple[Config, Path]:
    library_root = tmp_path / "library"
    library_root.mkdir()
    game_target = tmp_path / "game" / "Mods"
    game_target.mkdir(parents=True)
    config = add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kcd",
        targets=[game_target],
        library_subpath="KCD2/Mods",
        deploy_layout="mod_subdir",
    )
    return config, game_target


def _add_kcd2_mod(
    config: Config,
    state: State,
    tmp_path: Path,
    *,
    name: str,
) -> State:
    source = tmp_path / "incoming" / name
    source.mkdir(parents=True)
    (source / "mod.manifest").write_text("<kcd_mod/>", encoding="utf-8")
    data_dir = source / "Data"
    data_dir.mkdir()
    (data_dir / f"{name}.pak").write_bytes(b"pak")
    updated, _, _ = import_mod(config, state, source, game_id="kcd2")
    return updated


def test_mod_subdir_deploy_kcd2_layout(
    tmp_path: Path,
) -> None:
    config, game_target = _mod_subdir_config(tmp_path)
    state = _add_kcd2_mod(config, State(), tmp_path, name="moda")
    updated, outcome = deploy_game(config, state, "kcd2")
    manifest = game_target / "moda" / "mod.manifest"
    pak = game_target / "moda" / "Data" / "moda.pak"
    assert manifest.is_symlink()
    assert pak.is_symlink()
    assert (
        manifest.resolve()
        == (config.library_root / "KCD2/Mods/moda/mod.manifest").resolve()
    )
    assert outcome.links_created == 2
    assert len(updated.mods[0].deployed_links) == 2


def test_mod_subdir_two_mods_no_manifest_collision(
    tmp_path: Path,
) -> None:
    config, game_target = _mod_subdir_config(tmp_path)
    state = _add_kcd2_mod(config, State(), tmp_path, name="moda")
    state = _add_kcd2_mod(config, state, tmp_path, name="modb")
    updated, outcome = deploy_game(config, state, "kcd2")
    assert outcome.conflicts == []
    assert (game_target / "moda" / "mod.manifest").is_symlink()
    assert (game_target / "modb" / "mod.manifest").is_symlink()
    assert len(updated.mods) == 2


def test_flat_deploy_loose_pak(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    paks_target = tmp_path / "game" / "Phoenix" / "Content" / "Paks"
    paks_target.mkdir(parents=True)
    config = add_game_profile(
        Config(library_root=library_root),
        "hogwarts",
        nexus_domain="hogwartslegacy",
        targets=[paks_target],
        deploy_layout="flat",
    )
    source = tmp_path / "incoming" / "broom"
    source.mkdir(parents=True)
    (source / "zBroom_P.pak").write_bytes(b"pak")
    updated, record, _ = import_mod(config, State(), source, game_id="hogwarts")
    deploy_game(config, updated, "hogwarts")
    link = paks_target / "zBroom_P.pak"
    assert link.is_symlink()
    assert link.resolve() == (record.source_path / "zBroom_P.pak").resolve()


def test_mirror_deploy_preserves_game_relative_paths(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    game_root = tmp_path / "game" / "OblivionRemastered"
    game_root.mkdir(parents=True)
    config = add_game_profile(
        Config(library_root=library_root),
        "oblivion",
        nexus_domain="oblivionremastered",
        targets=[game_root],
        deploy_layout="mirror",
    )
    source = tmp_path / "incoming" / "betterhud"
    pak_dir = source / "Content" / "Paks" / "~mods"
    pak_dir.mkdir(parents=True)
    (pak_dir / "000_BetterHUD_P.pak").write_bytes(b"pak")
    updated, record, _ = import_mod(config, State(), source, game_id="oblivion")
    deploy_game(config, updated, "oblivion")
    link = game_root / "Content" / "Paks" / "~mods" / "000_BetterHUD_P.pak"
    assert link.is_symlink()
    assert (
        link.resolve()
        == (
            record.source_path / "Content" / "Paks" / "~mods" / "000_BetterHUD_P.pak"
        ).resolve()
    )


def test_resolve_link_path_mod_subdir(
    tmp_path: Path,
) -> None:
    profile = GameProfile(
        nexus_domain="kcd",
        targets=[tmp_path / "Mods"],
        deploy_layout="mod_subdir",
    )
    mod = ModRecord(name="moda", game="kcd2", source_path=tmp_path / "src")
    source_root = tmp_path / "src"
    source_file = source_root / "mod.manifest"
    link = resolve_link_path(
        profile,
        mod,
        tmp_path / "Mods",
        source_file,
        source_root,
    )
    assert link == tmp_path / "Mods" / "moda" / "mod.manifest"
