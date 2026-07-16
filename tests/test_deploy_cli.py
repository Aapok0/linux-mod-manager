"""CLI tests for deploy commands."""

from __future__ import annotations

import zipfile
from pathlib import Path

from typer.testing import CliRunner

from helpers import plain_cli_output
from lmm.archive import DOWNLOAD_DIRNAME
from lmm.cli import app
from lmm.state import StateStore


def _make_mod_zip(path: Path, mod_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{mod_name}/file.txt", "x")


def _make_package_dir(mod_dir: Path, mod_name: str) -> None:
    (mod_dir / DOWNLOAD_DIRNAME).mkdir(parents=True)
    (mod_dir / DOWNLOAD_DIRNAME / f"{mod_name}.zip").write_bytes(b"zip")
    (mod_dir / "file.txt").write_text("x", encoding="utf-8")


def test_cli_deploy_and_undeploy(
    runner: CliRunner,
    cli_args: list[str],
    game_target: Path,
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    archive = data_dir / "incoming" / "moda.zip"
    _make_mod_zip(archive, "moda")
    runner.invoke(app, [*cli_args, "add", str(archive), "--game", "kcd2"])

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
    mod_dir = data_dir / "library" / "KCD2" / "Mods" / "easysharpening"
    _make_package_dir(mod_dir, "easysharpening")

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


def test_cli_add_target_path_and_deploy(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    custom = data_dir / "custom" / "deploy"
    custom.mkdir(parents=True)
    archive = data_dir / "incoming" / "moda.zip"
    _make_mod_zip(archive, "moda")

    add = runner.invoke(
        app,
        [
            *cli_args,
            "add",
            str(archive),
            "--game",
            "kcd2",
            "--target-path",
            str(custom),
        ],
    )
    assert add.exit_code == 0, add.output

    deploy = runner.invoke(app, [*cli_args, "deploy", "kcd2"])
    assert deploy.exit_code == 0, deploy.output
    assert (custom / "file.txt").is_symlink()


def test_cli_add_move_removes_source(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    archive = data_dir / "incoming" / "movable.zip"
    _make_mod_zip(archive, "movable")

    result = runner.invoke(
        app,
        [*cli_args, "add", str(archive), "--game", "kcd2", "--move"],
    )
    assert result.exit_code == 0, result.output
    assert not archive.exists()
    library_mod = data_dir / "library" / "KCD2" / "Mods" / "movable"
    assert library_mod.is_dir()
    assert (library_mod / "file.txt").exists()


def test_cli_remove_delete_files_requires_yes(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_with_mod_minimal: Path,
) -> None:
    result = runner.invoke(
        app,
        [*cli_args, "remove", "mod", "--game", "kcd2", "--delete-files"],
    )
    assert result.exit_code == 1
    assert "--yes" in result.output


def test_cli_remove_delete_files_with_yes(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_with_mod_minimal: Path,
    data_dir: Path,
) -> None:
    library_mod = data_dir / "library" / "kcd2" / "mod"
    assert library_mod.is_dir()

    result = runner.invoke(
        app,
        [
            *cli_args,
            "remove",
            "mod",
            "--game",
            "kcd2",
            "--delete-files",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not library_mod.exists()
    state = StateStore(data_dir / "state.json").load()
    assert state.mods == []


def test_cli_add_rejects_target_index_and_path(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    archive = data_dir / "incoming" / "moda.zip"
    _make_mod_zip(archive, "moda")

    result = runner.invoke(
        app,
        [
            *cli_args,
            "add",
            str(archive),
            "--game",
            "kcd2",
            "--target-index",
            "0",
            "--target-path",
            "/tmp/custom",
        ],
        color=False,
    )
    assert result.exit_code == 2
    output = plain_cli_output(result.output)
    assert "Use only one of --target-index or --target-path" in output


def test_cli_add_all_imports_staging_directory(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    staging = data_dir / "incoming" / "batch"
    staging.mkdir(parents=True)
    for name in ("moda", "modb"):
        _make_mod_zip(staging / f"{name}.zip", name)

    result = runner.invoke(
        app,
        [*cli_args, "add", str(staging), "--game", "kcd2", "--all"],
    )
    assert result.exit_code == 0, result.output
    state = StateStore(data_dir / "state.json").load()
    assert {mod.name for mod in state.mods} == {"moda", "modb"}


def test_cli_add_all_move_removes_staging_children(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    staging = data_dir / "incoming" / "batch"
    staging.mkdir(parents=True)
    archive = staging / "movable.zip"
    _make_mod_zip(archive, "movable")

    result = runner.invoke(
        app,
        [*cli_args, "add", str(staging), "--game", "kcd2", "--all", "--move"],
    )
    assert result.exit_code == 0, result.output
    assert not archive.exists()


def test_cli_add_all_json_payload(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    staging = data_dir / "incoming" / "batch"
    staging.mkdir(parents=True)
    _make_mod_zip(staging / "moda.zip", "moda")
    (staging / "readme.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(
        app,
        [*cli_args, "--json", "add", str(staging), "--game", "kcd2", "--all"],
    )
    assert result.exit_code == 0, result.output
    assert '"imported"' in result.output
    assert '"skipped"' in result.output
    assert '"failures"' in result.output
    assert "unsupported_file" in result.output


def test_cli_add_all_rejects_name_override(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    staging = data_dir / "incoming" / "batch"
    staging.mkdir(parents=True)
    result = runner.invoke(
        app,
        [
            *cli_args,
            "add",
            str(staging),
            "--game",
            "kcd2",
            "--all",
            "--name",
            "custom",
        ],
        color=False,
    )
    assert result.exit_code == 2
    output = plain_cli_output(result.output)
    assert "--all cannot be combined" in output


def test_cli_add_all_dry_run(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    staging = data_dir / "incoming" / "batch"
    staging.mkdir(parents=True)
    _make_mod_zip(staging / "moda.zip", "moda")

    result = runner.invoke(
        app,
        [*cli_args, "--dry-run", "add", str(staging), "--game", "kcd2", "--all"],
    )
    assert result.exit_code == 0, result.output
    assert "Imported 1 mod(s)" in result.output
    state = StateStore(data_dir / "state.json").load()
    assert state.mods == []


def test_cli_update_single_mod(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    game_target: Path,
    data_dir: Path,
) -> None:
    archive_v1 = data_dir / "incoming" / "moda.zip"
    _make_mod_zip(archive_v1, "moda")
    runner.invoke(app, [*cli_args, "add", str(archive_v1), "--game", "kcd2"])
    runner.invoke(app, [*cli_args, "deploy", "kcd2"])

    archive_v2 = data_dir / "incoming" / "moda-v2.zip"
    with zipfile.ZipFile(archive_v2, "w") as zf:
        zf.writestr("moda/file.txt", "updated")

    result = runner.invoke(
        app,
        [
            *cli_args,
            "update",
            "moda",
            str(archive_v2),
            "--game",
            "kcd2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (game_target / "file.txt").read_text(encoding="utf-8") == "updated"


def test_cli_update_all_only_updates(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
) -> None:
    staging = data_dir / "incoming" / "batch"
    staging.mkdir(parents=True)
    _make_mod_zip(staging / "moda.zip", "moda")
    _make_mod_zip(staging / "modb.zip", "modb")
    runner.invoke(
        app,
        [*cli_args, "add", str(staging), "--game", "kcd2", "--all"],
    )
    state = StateStore(data_dir / "state.json").load()
    state.mods[0] = state.mods[0].model_copy(update={"update_available": True})
    StateStore(data_dir / "state.json").save(state)

    updates = data_dir / "incoming" / "updates"
    updates.mkdir(parents=True)
    _make_mod_zip(updates / "moda.zip", "moda")
    _make_mod_zip(updates / "modb.zip", "modb")

    result = runner.invoke(
        app,
        [
            *cli_args,
            "update",
            str(updates),
            "--game",
            "kcd2",
            "--all",
            "--only-updates",
            "--no-deploy",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "SKIP modb.zip: not_flagged" in result.output
