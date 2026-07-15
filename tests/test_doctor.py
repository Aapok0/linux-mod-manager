"""Tests for lmm doctor setup validation."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lmm.cli import app
from lmm.config import Config, ConfigStore, add_game_profile
from lmm.doctor import doctor_has_errors, run_doctor
from lmm.state import DeployedLink, ModRecord, State, StateStore


def _stores(
    tmp_path: Path,
    *,
    config: Config | None = None,
    state: State | None = None,
) -> tuple[ConfigStore, StateStore]:
    config_path = tmp_path / "config.toml"
    state_path = tmp_path / "state.json"
    library_root = tmp_path / "library"
    library_root.mkdir(exist_ok=True)
    if config is None:
        config = Config(library_root=library_root)
    if state is None:
        state = State()
    ConfigStore(config_path).save(config)
    StateStore(state_path).save(state)
    return ConfigStore(config_path), StateStore(state_path)


def _healthy_config(tmp_path: Path) -> Config:
    library_root = tmp_path / "library"
    library_root.mkdir(exist_ok=True)
    game_target = tmp_path / "game" / "Mods"
    game_target.mkdir(parents=True)
    return add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kcd",
        targets=[game_target],
        library_subpath="KCD2/Mods",
    )


def _check_by_name(checks: list, name: str):
    for check in checks:
        if check.name == name:
            return check
    msg = f"Check not found: {name}"
    raise AssertionError(msg)


def test_doctor_healthy_setup(tmp_path: Path) -> None:
    config_store, state_store = _stores(tmp_path, config=_healthy_config(tmp_path))
    checks = run_doctor(config_store, state_store)
    library = _check_by_name(checks, "library_root")
    assert library.status == "ok"
    assert doctor_has_errors(checks) is False


def test_doctor_missing_library(tmp_path: Path) -> None:
    config = Config(library_root=tmp_path / "missing" / "library")
    config_store, state_store = _stores(tmp_path, config=config)
    checks = run_doctor(config_store, state_store)
    library = _check_by_name(checks, "library_root")
    assert library.status == "error"
    assert doctor_has_errors(checks) is True


def test_doctor_unwritable_library(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    config = _healthy_config(tmp_path)
    config_store, state_store = _stores(tmp_path, config=config)
    library_root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        checks = run_doctor(config_store, state_store)
        library = _check_by_name(checks, "library_root")
        assert library.status == "error"
        assert doctor_has_errors(checks) is True
    finally:
        library_root.chmod(stat.S_IRWXU)


def test_doctor_missing_mod_source(tmp_path: Path) -> None:
    config = _healthy_config(tmp_path)
    missing = tmp_path / "library" / "KCD2" / "Mods" / "gone"
    state = State(
        mods=[
            ModRecord(
                name="gone",
                game="kcd2",
                source_path=missing,
            )
        ]
    )
    config_store, state_store = _stores(tmp_path, config=config, state=state)
    checks = run_doctor(config_store, state_store)
    mod_check = _check_by_name(checks, "mod.kcd2/gone")
    assert mod_check.status == "warning"
    assert "missing" in mod_check.message.lower()


def test_doctor_enabled_not_deployed(tmp_path: Path) -> None:
    config = _healthy_config(tmp_path)
    source = tmp_path / "library" / "KCD2" / "Mods" / "moda"
    source.mkdir(parents=True)
    state = State(
        mods=[
            ModRecord(
                name="moda",
                game="kcd2",
                source_path=source,
                enabled=True,
            )
        ]
    )
    config_store, state_store = _stores(tmp_path, config=config, state=state)
    checks = run_doctor(config_store, state_store)
    deploy_hint = _check_by_name(checks, "mod.kcd2/moda.deploy")
    assert deploy_hint.status == "info"
    assert "deploy" in deploy_hint.message.lower()


def test_doctor_corrupt_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    state_path = tmp_path / "state.json"
    config_path.write_text("[[games]\n", encoding="utf-8")
    StateStore(state_path).save(State())
    checks = run_doctor(ConfigStore(config_path), StateStore(state_path))
    config_check = _check_by_name(checks, "config")
    assert config_check.status == "error"
    assert len(checks) == 1


def test_doctor_missing_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEXUS_API_KEY", raising=False)
    config_store, state_store = _stores(tmp_path, config=_healthy_config(tmp_path))
    checks = run_doctor(config_store, state_store)
    key_check = _check_by_name(checks, "nexus_api_key")
    assert key_check.status == "info"
    assert "not set" in key_check.message.lower()


def test_doctor_mod_subdir_flat_deploy_warning(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir(exist_ok=True)
    game_target = tmp_path / "game" / "Mods"
    game_target.mkdir(parents=True)
    config = add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kcd",
        targets=[game_target],
        deploy_layout="mod_subdir",
    )
    source = library_root / "KCD2" / "Mods" / "moda"
    source.mkdir(parents=True)
    (source / "mod.manifest").write_text("<kcd_mod/>", encoding="utf-8")
    manifest = game_target / "mod.manifest"
    manifest.symlink_to(source / "mod.manifest")
    state = State(
        mods=[
            ModRecord(
                name="moda",
                game="kcd2",
                source_path=source,
                deployed_links=[
                    DeployedLink(
                        link=manifest,
                        source=source / "mod.manifest",
                    )
                ],
            )
        ]
    )
    config_store, state_store = _stores(tmp_path, config=config, state=state)
    checks = run_doctor(config_store, state_store)
    layout = _check_by_name(checks, "mod.kcd2/moda.layout")
    assert layout.status == "warning"
    assert "mod_subdir" in layout.message


def test_doctor_mirror_flat_source_warning(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir(exist_ok=True)
    game_root = tmp_path / "game" / "OblivionRemastered"
    game_root.mkdir(parents=True)
    config = add_game_profile(
        Config(library_root=library_root),
        "oblivion",
        nexus_domain="oblivionremastered",
        targets=[game_root],
        deploy_layout="mirror",
    )
    source = library_root / "oblivion" / "flatmod"
    source.mkdir(parents=True)
    (source / "mod.pak").write_bytes(b"pak")
    state = State(
        mods=[
            ModRecord(
                name="flatmod",
                game="oblivion",
                source_path=source,
            )
        ]
    )
    config_store, state_store = _stores(tmp_path, config=config, state=state)
    checks = run_doctor(config_store, state_store)
    layout = _check_by_name(checks, "mod.oblivion/flatmod.layout")
    assert layout.status == "warning"
    assert "mirror layout" in layout.message


@pytest.mark.integration
def test_cli_doctor_json_exits_on_errors(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LMM_LIBRARY_ROOT", raising=False)
    config = Config(library_root=data_dir / "missing-library")
    ConfigStore(data_dir / "config.toml").save(config)

    result = runner.invoke(app, [*cli_args, "--json", "doctor"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert any(item["status"] == "error" for item in payload)
