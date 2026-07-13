"""CLI tests for deploy commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from lmm.cli import app
from lmm.state import StateStore


def test_cli_deploy_and_undeploy(
    runner: CliRunner,
    cli_args: list[str],
    game_target: Path,
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    mod_source = data_dir / "incoming" / "moda"
    mod_source.mkdir(parents=True)
    (mod_source / "file.txt").write_text("x", encoding="utf-8")
    runner.invoke(app, [*cli_args, "add", str(mod_source), "--game", "kcd2"])

    deploy = runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    assert deploy.exit_code == 0, deploy.output
    assert (game_target / "file.txt").is_symlink()

    state = StateStore(data_dir / "state.json").load()
    assert len(state.mods[0].deployed_links) == 1

    undeploy = runner.invoke(app, [*cli_args, "undeploy", "kcd2", "--yes"])
    assert undeploy.exit_code == 0, undeploy.output
    assert not (game_target / "file.txt").exists()


def test_cli_add_bare_mod_name(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    kcd2_profile: None,
) -> None:
    library_root = data_dir / "library"
    mod_dir = library_root / "KCD2/Mods/easysharpening"
    mod_dir.mkdir(parents=True)

    result = runner.invoke(app, [*cli_args, "add", "easysharpening", "--game", "kcd2"])
    assert result.exit_code == 0, result.output

    state = StateStore(data_dir / "state.json").load()
    assert state.mods[0].name == "easysharpening"


def test_cli_enable_disable(
    runner: CliRunner,
    cli_args: list[str],
    game_target: Path,
    kcd2_with_mod_minimal: Path,
) -> None:
    disable = runner.invoke(app, [*cli_args, "disable", "mod", "--game", "kcd2"])
    assert disable.exit_code == 0, disable.output

    deploy = runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    assert deploy.exit_code == 0, deploy.output
    assert not (game_target / "a.txt").exists()

    enable = runner.invoke(app, [*cli_args, "enable", "mod", "--game", "kcd2"])
    assert enable.exit_code == 0, enable.output

    deploy2 = runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    assert deploy2.exit_code == 0, deploy2.output


def test_cli_disable_after_deploy_removes_links(
    runner: CliRunner,
    cli_args: list[str],
    game_target: Path,
    kcd2_with_mod_minimal: Path,
    data_dir: Path,
) -> None:
    runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    assert (game_target / "a.txt").is_symlink()

    disable = runner.invoke(app, [*cli_args, "disable", "mod", "--game", "kcd2"])
    assert disable.exit_code == 0, disable.output
    assert (game_target / "a.txt").is_symlink()

    deploy = runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    assert deploy.exit_code == 0, deploy.output
    assert not (game_target / "a.txt").exists()
    state = StateStore(data_dir / "state.json").load()
    assert state.mods[0].enabled is False
    assert len(state.mods[0].deployed_links) == 0


def test_cli_enable_disable_missing_mod_reports_error(
    runner: CliRunner,
    cli_args: list[str],
) -> None:
    disable = runner.invoke(app, [*cli_args, "disable", "missing-mod"])
    assert disable.exit_code == 1
    assert "Mod not found" in disable.output
    assert "Traceback" not in disable.output

    enable = runner.invoke(app, [*cli_args, "enable", "missing-mod"])
    assert enable.exit_code == 1
    assert "Mod not found" in enable.output
    assert "Traceback" not in enable.output


def test_cli_dry_run_deploy(
    runner: CliRunner,
    cli_args: list[str],
    game_target: Path,
    kcd2_with_mod_minimal: Path,
    data_dir: Path,
) -> None:
    result = runner.invoke(app, [*cli_args, "--dry-run", "deploy", "kcd2"])
    assert result.exit_code == 0, result.output
    assert not (game_target / "a.txt").exists()
    state = StateStore(data_dir / "state.json").load()
    assert len(state.mods[0].deployed_links) == 0


def test_cli_deploy_json_output(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_with_mod_minimal: Path,
) -> None:
    result = runner.invoke(app, [*cli_args, "--json", "deploy", "kcd2"])
    assert result.exit_code == 0, result.output
    assert '"links_created": 1' in result.output
    assert '"game": "kcd2"' in result.output


def test_cli_deploy_conflict_exits_nonzero(
    runner: CliRunner,
    cli_args: list[str],
    game_target: Path,
    kcd2_with_mod_minimal: Path,
) -> None:
    (game_target / "a.txt").write_text("blocked", encoding="utf-8")
    result = runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    assert result.exit_code == 1, result.output
    assert "CONFLICT:" in result.output
    assert "partial failure" in result.output


def test_cli_undeploy_requires_yes_in_non_interactive(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_with_mod_minimal: Path,
    game_target: Path,
) -> None:
    runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    result = runner.invoke(app, [*cli_args, "undeploy", "kcd2"])
    assert result.exit_code == 1
    assert "--yes" in result.output


def test_cli_remove_mod(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_with_mod_minimal: Path,
    data_dir: Path,
) -> None:
    runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    result = runner.invoke(app, [*cli_args, "remove", "mod", "--game", "kcd2", "--yes"])
    assert result.exit_code == 0, result.output
    state = StateStore(data_dir / "state.json").load()
    assert state.mods == []


def test_cli_doctor_ok(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
) -> None:
    result = runner.invoke(app, [*cli_args, "doctor"])
    assert result.exit_code == 0, result.output
    assert "library_root" in result.output
