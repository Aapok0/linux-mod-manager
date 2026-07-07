"""CLI tests for deploy commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from lmm.cli import app
from lmm.state import StateStore


def test_cli_deploy_and_undeploy(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    game_target: Path,
) -> None:
    runner.invoke(
        app,
        [
            *cli_args,
            "game",
            "add",
            "kcd2",
            "--domain",
            "kcd",
            "--target",
            str(game_target),
            "--library-subpath",
            "KCD2/Mods",
        ],
    )
    mod_source = data_dir / "incoming" / "moda"
    mod_source.mkdir(parents=True)
    (mod_source / "file.txt").write_text("x", encoding="utf-8")
    runner.invoke(app, [*cli_args, "add", str(mod_source), "--game", "kcd2"])

    deploy = runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    assert deploy.exit_code == 0, deploy.output
    assert (game_target / "file.txt").is_symlink()

    state = StateStore(data_dir / "state.json").load()
    assert len(state.mods[0].deployed_links) == 1

    undeploy = runner.invoke(app, [*cli_args, "undeploy", "kcd2"])
    assert undeploy.exit_code == 0, undeploy.output
    assert not (game_target / "file.txt").exists()


def test_cli_add_bare_mod_name(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    game_target: Path,
) -> None:
    runner.invoke(
        app,
        [
            *cli_args,
            "game",
            "add",
            "kcd2",
            "--domain",
            "kcd",
            "--target",
            str(game_target),
            "--library-subpath",
            "KCD2/Mods",
        ],
    )
    library_root = data_dir / "library"
    mod_dir = library_root / "KCD2/Mods/easysharpening"
    mod_dir.mkdir(parents=True)

    result = runner.invoke(app, [*cli_args, "add", "easysharpening", "--game", "kcd2"])
    assert result.exit_code == 0, result.output

    state = StateStore(data_dir / "state.json").load()
    assert state.mods[0].name == "easysharpening"


def test_cli_enable_disable(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    game_target: Path,
) -> None:
    runner.invoke(
        app,
        [
            *cli_args,
            "game",
            "add",
            "kcd2",
            "--domain",
            "kcd",
            "--target",
            str(game_target),
        ],
    )
    mod_source = data_dir / "mod"
    mod_source.mkdir()
    (mod_source / "a.txt").write_text("a", encoding="utf-8")
    runner.invoke(app, [*cli_args, "add", str(mod_source), "--game", "kcd2"])

    disable = runner.invoke(app, [*cli_args, "disable", "mod", "--game", "kcd2"])
    assert disable.exit_code == 0, disable.output

    deploy = runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    assert deploy.exit_code == 0, deploy.output
    assert not (game_target / "a.txt").exists()

    enable = runner.invoke(app, [*cli_args, "enable", "mod", "--game", "kcd2"])
    assert enable.exit_code == 0, enable.output

    deploy2 = runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    assert deploy2.exit_code == 0, deploy2.output


def test_cli_dry_run_deploy(
    runner: CliRunner,
    data_dir: Path,
    cli_args: list[str],
    game_target: Path,
) -> None:
    runner.invoke(
        app,
        [
            *cli_args,
            "game",
            "add",
            "kcd2",
            "--domain",
            "kcd",
            "--target",
            str(game_target),
        ],
    )
    mod_source = data_dir / "mod"
    mod_source.mkdir()
    (mod_source / "a.txt").write_text("a", encoding="utf-8")
    runner.invoke(app, [*cli_args, "add", str(mod_source), "--game", "kcd2"])

    result = runner.invoke(app, [*cli_args, "--dry-run", "deploy", "kcd2"])
    assert result.exit_code == 0, result.output
    assert not (game_target / "a.txt").exists()
    state = StateStore(data_dir / "state.json").load()
    assert len(state.mods[0].deployed_links) == 0
