"""Typer CLI for lmm."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypeVar

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from lmm import __version__
from lmm.config import (
    ConfigError,
    ConfigStore,
    DeployLayout,
    add_game_profile,
    add_game_target,
    remove_game_target,
)
from lmm.deploy import DeployError, deploy_game, remove_mod, undeploy_game
from lmm.doctor import doctor_has_errors, run_doctor
from lmm.library import (
    ImportAction,
    ImportFailure,
    ImportResult,
    ImportSkip,
    LibraryError,
    import_mod,
    import_mods_from_directory,
    list_mods,
    mod_is_deployed,
    resolve_mod_source,
)
from lmm.logging_config import setup_logging
from lmm.nexus import NexusClient, NexusError
from lmm.nexus.link import LinkError, link_mod_record
from lmm.nexus.updates import (
    IdentifySkip,
    check_for_updates,
    identify_mods,
    plan_check,
    plan_identify,
    unlinked_mods,
)
from lmm.state import (
    StateError,
    StateStore,
    adjust_mod_targets_after_remove,
    find_mod,
    mods_referencing_target_index,
    set_mod_enabled,
)

app = typer.Typer(
    name="lmm",
    no_args_is_help=True,
    help=(
        "Linux Mod Manager (lmm): game add → add → deploy → check. "
        "Symlink-based mod deployment with Nexus version checking."
    ),
)
game_app = typer.Typer(help="Manage game profiles.")
app.add_typer(game_app, name="game")
target_app = typer.Typer(help="Manage deploy targets for a game profile.")
game_app.add_typer(target_app, name="target")
mod_app = typer.Typer(help="Mod metadata and Nexus linking.")
app.add_typer(mod_app, name="mod")

console = Console()
stderr_console = Console(stderr=True)

T = TypeVar("T")


@dataclass
class AppContext:
    config_path: Path | None
    state_path: Path | None
    as_json: bool
    dry_run: bool
    verbose: bool


def _ctx(ctx: typer.Context) -> AppContext:
    obj = ctx.obj
    if not isinstance(obj, AppContext):
        msg = "CLI context not initialized"
        raise RuntimeError(msg)
    return obj


def _config_store(ctx: AppContext) -> ConfigStore:
    return ConfigStore(ctx.config_path)


def _state_store(ctx: AppContext) -> StateStore:
    return StateStore(ctx.state_path)


def _handle_errors(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except (ConfigError, DeployError, LibraryError, NexusError, StateError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def _nexus_client(app_ctx: AppContext, config_store: ConfigStore) -> NexusClient:
    config = config_store.load()
    api_key = config_store.resolve_api_key(config)
    return NexusClient(api_key=api_key)


def _truncate_path(path: Path, max_len: int = 60) -> str:
    text = str(path)
    if len(text) <= max_len:
        return text
    return f"…{text[-(max_len - 1) :]}"


def _confirm_action(*, yes: bool, dry_run: bool, prompt: str) -> None:
    if dry_run or yes:
        return
    if sys.stdin.isatty():
        if not typer.confirm(prompt, default=False):
            raise typer.Exit(1)
        return
    typer.echo("Pass --yes to confirm in non-interactive mode", err=True)
    raise typer.Exit(1)


def _count_game_links(state, game_id: str) -> int:
    return sum(len(mod.deployed_links) for mod in state.mods if mod.game == game_id)


def _identify_degraded(
    state,
    game_id: str,
    skips: list[IdentifySkip],
    failures: list[object],
) -> bool:
    if failures:
        return True
    if unlinked_mods(state, game_id):
        return True
    return any(skip.reason in ("no_download_file", "no_nexus_match") for skip in skips)


def _deploy_apply_hint(game: str) -> None:
    console.print(f"Run [bold]lmm deploy {game}[/bold] to apply.")


def _import_action_message(action: ImportAction, record) -> str:
    if action == ImportAction.COPIED:
        return (
            f"Copied mod [bold]{record.game}/{record.name}[/bold] "
            f"to {record.source_path}"
        )
    if action == ImportAction.MOVED:
        return (
            f"Moved mod [bold]{record.game}/{record.name}[/bold] "
            f"to {record.source_path}"
        )
    if action == ImportAction.EXTRACTED:
        return (
            f"Imported mod [bold]{record.game}/{record.name}[/bold] "
            f"to {record.source_path}"
        )
    return (
        f"Registered mod [bold]{record.game}/{record.name}[/bold] "
        f"in place at {record.source_path}"
    )


def _bulk_import_json_payload(
    results: list[ImportResult],
    failures: list[ImportFailure],
    skips: list[ImportSkip],
) -> dict[str, object]:
    return {
        "imported": [
            {
                **item.record.model_dump(mode="json"),
                "import_action": item.action.value,
            }
            for item in results
        ],
        "skipped": [{"path": str(item.path), "reason": item.reason} for item in skips],
        "failures": [{"name": item.name, "error": item.error} for item in failures],
    }


def _print_bulk_import_summary(
    *,
    dry_run: bool,
    results: list[ImportResult],
    failures: list[ImportFailure],
    skips: list[ImportSkip],
) -> None:
    prefix = "[dry-run] " if dry_run else ""
    if results:
        imported = ", ".join(
            f"{item.record.name} ({item.action.value})" for item in results
        )
        console.print(f"{prefix}Imported {len(results)} mod(s): {imported}")
    elif not failures and not skips:
        console.print(f"{prefix}No mods found to import.")
    for item in skips:
        console.print(f"Skipped: {item.path.name} ({item.reason})")
    for item in failures:
        console.print(f"FAIL: {item.name}: {item.error}")


@app.callback()
def app_callback(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to config.toml"),
    ] = None,
    state: Annotated[
        Path | None,
        typer.Option("--state", help="Path to state.json"),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print actions without making changes"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging"),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", help="Show version and exit"),
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    setup_logging(verbose=verbose)
    ctx.obj = AppContext(
        config_path=config,
        state_path=state,
        as_json=as_json,
        dry_run=dry_run,
        verbose=verbose,
    )


@game_app.command("add")
def game_add(
    ctx: typer.Context,
    game_id: Annotated[str, typer.Argument(help="Short game id (e.g. kcd2)")],
    domain: Annotated[
        str,
        typer.Option("--domain", help="Nexus game domain name"),
    ],
    target: Annotated[
        list[Path],
        typer.Option("--target", help="Deploy target path (repeatable)"),
    ],
    library_subpath: Annotated[
        str | None,
        typer.Option(
            "--library-subpath",
            help="Subpath under library_root for this game's mods (default: game id)",
        ),
    ] = None,
    deploy_layout: Annotated[
        DeployLayout,
        typer.Option(
            "--deploy-layout",
            help="How mod files map into deploy targets (flat, mod_subdir, mirror)",
        ),
    ] = "flat",
) -> None:
    """Register a game profile."""
    if not target:
        raise typer.BadParameter("At least one --target is required")

    def run() -> None:
        app_ctx = _ctx(ctx)
        store = _config_store(app_ctx)
        config = store.load()
        try:
            updated = add_game_profile(
                config,
                game_id,
                nexus_domain=domain,
                targets=target,
                library_subpath=library_subpath,
                deploy_layout=deploy_layout,
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        store.save(updated)
        if app_ctx.as_json:
            typer.echo(
                updated.games[game_id].model_dump_json(indent=2),
            )
            return
        console.print(f"Added game profile [bold]{game_id}[/bold]")

    _handle_errors(run)


@game_app.command("list")
def game_list(ctx: typer.Context) -> None:
    """List configured game profiles."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        config = _config_store(app_ctx).load()
        if app_ctx.as_json:
            payload = {
                game_id: profile.model_dump(mode="json")
                for game_id, profile in config.games.items()
            }
            typer.echo(json.dumps(payload, indent=2))
            return
        if not config.games:
            console.print("No game profiles configured.")
            return
        table = Table(title="Game profiles")
        table.add_column("ID")
        table.add_column("Nexus domain")
        table.add_column("Deploy layout")
        table.add_column("Targets")
        table.add_column("Library subpath")
        for game_id, profile in sorted(config.games.items()):
            targets = "\n".join(
                f"[{index}] {path}" for index, path in enumerate(profile.targets)
            )
            table.add_row(
                game_id,
                profile.nexus_domain,
                profile.deploy_layout,
                targets,
                profile.library_subpath or "",
            )
        console.print(table)

    _handle_errors(run)


@target_app.command("add")
def game_target_add(
    ctx: typer.Context,
    game_id: Annotated[str, typer.Argument(help="Short game id (e.g. kcd2)")],
    target: Annotated[
        list[Path],
        typer.Option("--target", help="Deploy target path (repeatable)"),
    ],
) -> None:
    """Add deploy target(s) to an existing game profile."""
    if not target:
        raise typer.BadParameter("At least one --target is required")

    def run() -> None:
        app_ctx = _ctx(ctx)
        store = _config_store(app_ctx)
        config = store.load()
        updated = config
        try:
            for path in target:
                updated = add_game_target(updated, game_id, path)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        store.save(updated)
        if app_ctx.as_json:
            typer.echo(updated.games[game_id].model_dump_json(indent=2))
            return
        console.print(f"Added deploy target(s) to [bold]{game_id}[/bold]")

    _handle_errors(run)


@target_app.command("list")
def game_target_list(
    ctx: typer.Context,
    game_id: Annotated[str, typer.Argument(help="Short game id (e.g. kcd2)")],
) -> None:
    """List deploy targets for a game profile."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        config = _config_store(app_ctx).load()
        profile = config.games.get(game_id)
        if profile is None:
            raise typer.BadParameter(f"Unknown game profile: {game_id}")
        if app_ctx.as_json:
            payload = {
                "targets": [
                    {"index": index, "path": str(path)}
                    for index, path in enumerate(profile.targets)
                ]
            }
            typer.echo(json.dumps(payload, indent=2))
            return
        table = Table(title=f"Deploy targets for {game_id}")
        table.add_column("Index")
        table.add_column("Path")
        for index, path in enumerate(profile.targets):
            table.add_row(str(index), str(path))
        console.print(table)

    _handle_errors(run)


@target_app.command("remove")
def game_target_remove(
    ctx: typer.Context,
    game_id: Annotated[str, typer.Argument(help="Short game id (e.g. kcd2)")],
    index: Annotated[
        list[int],
        typer.Option("--index", help="Deploy target index to remove (repeatable)"),
    ],
) -> None:
    """Remove secondary deploy target(s) from a game profile."""
    if not index:
        raise typer.BadParameter("At least one --index is required")

    def run() -> None:
        app_ctx = _ctx(ctx)
        config_store = _config_store(app_ctx)
        state_store = _state_store(app_ctx)
        config = config_store.load()
        state = state_store.load()
        if game_id not in config.games:
            raise typer.BadParameter(f"Unknown game profile: {game_id}")

        indices = sorted(set(index), reverse=True)
        if app_ctx.dry_run:
            if app_ctx.as_json:
                payload = {
                    "dry_run": True,
                    "game": game_id,
                    "remove_indices": indices,
                }
                typer.echo(json.dumps(payload, indent=2))
                return
            prefix = "[dry-run] "
            console.print(
                f"{prefix}Would remove deploy target index(es) "
                f"[{', '.join(str(i) for i in indices)}] from {game_id}",
            )
            return

        for target_index in indices:
            refs = mods_referencing_target_index(state, game_id, target_index)
            if refs:
                names = ", ".join(mod.name for mod in refs)
                raise typer.BadParameter(
                    f"Deploy target index {target_index} is used by mod(s): {names}"
                )
            try:
                config = remove_game_target(config, game_id, target_index)
            except ValueError as exc:
                raise typer.BadParameter(str(exc)) from exc
            state = adjust_mod_targets_after_remove(state, game_id, target_index)

        config_store.save(config)
        state_store.save(state)
        if app_ctx.as_json:
            typer.echo(config.games[game_id].model_dump_json(indent=2))
            return
        removed = ", ".join(str(i) for i in sorted(set(index), reverse=True))
        console.print(
            f"Removed deploy target index(es) [{removed}] from [bold]{game_id}[/bold]"
        )

    _handle_errors(run)


@app.command("add")
def mod_add(
    ctx: typer.Context,
    name_or_path: Annotated[
        Path,
        typer.Argument(
            help="Nexus download file, directory of downloads, or library mod package"
        ),
    ],
    game: Annotated[str, typer.Option("--game", help="Game profile id")],
    name: Annotated[
        str | None,
        typer.Option("--name", help="Mod name (default: directory name)"),
    ] = None,
    mod_id: Annotated[
        int | None,
        typer.Option("--mod-id", help="Nexus mod id"),
    ] = None,
    mod_url: Annotated[
        str | None,
        typer.Option("--mod-url", help="Nexus mod page URL"),
    ] = None,
    target_index: Annotated[
        int | None,
        typer.Option("--target-index", help="Deploy target index override"),
    ] = None,
    target_path: Annotated[
        Path | None,
        typer.Option("--target-path", help="Absolute deploy target path override"),
    ] = None,
    move: Annotated[
        bool,
        typer.Option(
            "--move",
            help="Move mod tree into library instead of copying (outside library only)",
        ),
    ] = False,
    all_mods: Annotated[
        bool,
        typer.Option(
            "--all",
            help=(
                "Import each top-level download file (.zip, .7z, .rar, loose mod file)"
            ),
        ),
    ] = False,
) -> None:
    """Import a Nexus download and record it in state."""
    if mod_id is not None and mod_url is not None:
        raise typer.BadParameter("Use only one of --mod-id or --mod-url")
    if target_index is not None and target_path is not None:
        raise typer.BadParameter("Use only one of --target-index or --target-path")
    if all_mods and (
        name is not None
        or mod_id is not None
        or mod_url is not None
        or target_index is not None
        or target_path is not None
    ):
        raise typer.BadParameter(
            "--all cannot be combined with --name, --mod-id, --mod-url, "
            "or target overrides"
        )

    def run() -> None:
        app_ctx = _ctx(ctx)
        config_store = _config_store(app_ctx)
        state_store = _state_store(app_ctx)
        config = config_store.load()
        state = state_store.load()

        if all_mods:
            parent = name_or_path.resolve()
            updated_state, results, failures, skips = import_mods_from_directory(
                config,
                state,
                parent,
                game_id=game,
                copy=not move,
                dry_run=app_ctx.dry_run,
            )
            if not app_ctx.dry_run:
                state_store.save(updated_state)
            if app_ctx.as_json:
                typer.echo(
                    json.dumps(
                        _bulk_import_json_payload(results, failures, skips),
                        indent=2,
                    ),
                )
                if failures:
                    raise typer.Exit(1)
                return
            _print_bulk_import_summary(
                dry_run=app_ctx.dry_run,
                results=results,
                failures=failures,
                skips=skips,
            )
            if failures:
                raise typer.Exit(1)
            return

        source = resolve_mod_source(config, game, name_or_path)
        target_override: int | str | None
        if target_index is not None:
            target_override = target_index
        elif target_path is not None:
            target_override = str(target_path)
        else:
            target_override = None
        updated_state, record, action = import_mod(
            config,
            state,
            source,
            game_id=game,
            name=name,
            nexus_mod_id=mod_id,
            target=target_override,
            copy=not move,
        )
        if mod_id is not None or mod_url is not None:
            with _nexus_client(app_ctx, config_store) as client:
                client.validate_key()
                try:
                    record = link_mod_record(
                        config,
                        record,
                        client=client,
                        mod_id=mod_id,
                        url=mod_url,
                    )
                except (LinkError, NexusError) as exc:
                    raise LibraryError(str(exc)) from exc
                for index, entry in enumerate(updated_state.mods):
                    if entry.game == record.game and entry.name == record.name:
                        updated_state.mods[index] = record
                        break
        state_store.save(updated_state)
        if app_ctx.as_json:
            payload = record.model_dump(mode="json")
            payload["import_action"] = action.value
            typer.echo(json.dumps(payload, indent=2))
            return
        console.print(_import_action_message(action, record))

    _handle_errors(run)


@app.command("list")
def mod_list(
    ctx: typer.Context,
    game_arg: Annotated[
        str | None,
        typer.Argument(help="Filter by game profile id"),
    ] = None,
    game: Annotated[
        str | None,
        typer.Option(
            "--game",
            help="Filter by game profile id (alternative to positional)",
        ),
    ] = None,
) -> None:
    """List registered mods."""
    filter_game = game or game_arg

    def run() -> None:
        app_ctx = _ctx(ctx)
        state = _state_store(app_ctx).load()
        mods = list_mods(state, filter_game)
        if app_ctx.as_json:
            typer.echo(
                json.dumps([mod.model_dump(mode="json") for mod in mods], indent=2),
            )
            return
        if not mods:
            console.print("No mods registered.")
            return
        show_source = app_ctx.verbose
        table = Table(title="Mods")
        table.add_column("Game")
        table.add_column("Name")
        table.add_column("Version")
        table.add_column("Latest")
        table.add_column("Update")
        table.add_column("Enabled")
        table.add_column("Deployed")
        if show_source:
            table.add_column("Source")
        for mod in mods:
            update_cell = "UPDATE" if mod.update_available else "—"
            row = [
                mod.game,
                mod.name,
                mod.installed_version or "",
                mod.latest_version or "",
                update_cell,
                "yes" if mod.enabled else "no",
                "yes" if mod_is_deployed(mod) else "no",
            ]
            if show_source:
                row.append(_truncate_path(mod.source_path))
            table.add_row(*row)
        console.print(table)

    _handle_errors(run)


@mod_app.command("link")
def mod_link(
    ctx: typer.Context,
    mod: Annotated[str, typer.Argument(help="Mod name or game/name")],
    game: Annotated[
        str | None,
        typer.Option("--game", help="Game profile id when mod name is ambiguous"),
    ] = None,
    mod_id: Annotated[
        int | None,
        typer.Option("--mod-id", help="Nexus mod id"),
    ] = None,
    url: Annotated[
        str | None,
        typer.Option("--url", help="Nexus mod page URL"),
    ] = None,
) -> None:
    """Link a mod to Nexus metadata for identify/check."""

    def run() -> None:
        if mod_id is None and url is None:
            raise typer.BadParameter("Provide --mod-id or --url")
        if mod_id is not None and url is not None:
            raise typer.BadParameter("Use only one of --mod-id or --url")
        app_ctx = _ctx(ctx)
        config_store = _config_store(app_ctx)
        state_store = _state_store(app_ctx)
        config = config_store.load()
        state = state_store.load()
        record = find_mod(state, mod, default_game=game)
        with _nexus_client(app_ctx, config_store) as client:
            client.validate_key()
            try:
                linked = link_mod_record(
                    config,
                    record,
                    client=client,
                    mod_id=mod_id,
                    url=url,
                )
            except (LinkError, NexusError) as exc:
                raise LibraryError(str(exc)) from exc
        updated = state.model_copy(deep=True)
        for index, entry in enumerate(updated.mods):
            if entry.game == linked.game and entry.name == linked.name:
                updated.mods[index] = linked
                break
        state_store.save(updated)
        if app_ctx.as_json:
            typer.echo(json.dumps(linked.model_dump(mode="json"), indent=2))
            return
        console.print(
            f"Linked mod [bold]{linked.game}/{linked.name}[/bold] "
            f"to Nexus mod {linked.nexus_mod_id}"
        )

    _handle_errors(run)


@app.command("deploy")
def mod_deploy(
    ctx: typer.Context,
    game: Annotated[str, typer.Argument(help="Game profile id")],
) -> None:
    """Deploy enabled mods for a game via recorded symlinks."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        config = _config_store(app_ctx).load()
        state_store = _state_store(app_ctx)
        state = state_store.load()
        updated_state, outcome = deploy_game(
            config,
            state,
            game,
            dry_run=app_ctx.dry_run,
        )
        if not app_ctx.dry_run:
            state_store.save(updated_state)
        if app_ctx.as_json:
            payload = {
                "game": game,
                "dry_run": app_ctx.dry_run,
                "links_created": outcome.links_created,
                "links_removed": outcome.links_removed,
                "links_skipped": outcome.links_skipped,
                "conflicts": outcome.conflicts,
                "warnings": outcome.warnings,
            }
            typer.echo(json.dumps(payload, indent=2))
            if outcome.conflicts:
                raise typer.Exit(1)
            return
        prefix = "[dry-run] " if app_ctx.dry_run else ""
        summary = (
            f"{prefix}Deploy {game}: "
            f"{outcome.links_created} link(s) created, "
            f"{outcome.links_removed} removed, "
            f"{outcome.links_skipped} skipped"
        )
        if outcome.conflicts:
            summary += f", partial failure: {len(outcome.conflicts)} conflict(s)"
        console.print(summary)
        for conflict in outcome.conflicts:
            console.print(f"CONFLICT: {conflict}")
        for warning in outcome.warnings:
            console.print(f"WARNING: {warning}")
        if outcome.conflicts:
            raise typer.Exit(1)

    _handle_errors(run)


@app.command("undeploy")
def mod_undeploy(
    ctx: typer.Context,
    game: Annotated[str, typer.Argument(help="Game profile id")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt"),
    ] = False,
) -> None:
    """Remove deployed symlinks recorded for a game."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        config = _config_store(app_ctx).load()
        state_store = _state_store(app_ctx)
        state = state_store.load()
        link_count = _count_game_links(state, game)
        _confirm_action(
            yes=yes,
            dry_run=app_ctx.dry_run,
            prompt=f"Undeploy {link_count} link(s) for {game}?",
        )
        updated_state, outcome = undeploy_game(
            config,
            state,
            game,
            dry_run=app_ctx.dry_run,
        )
        if not app_ctx.dry_run:
            state_store.save(updated_state)
        if app_ctx.as_json:
            payload = {
                "game": game,
                "dry_run": app_ctx.dry_run,
                "links_removed": outcome.links_removed,
                "links_skipped": outcome.links_skipped,
                "warnings": outcome.warnings,
            }
            typer.echo(json.dumps(payload, indent=2))
            return
        prefix = "[dry-run] " if app_ctx.dry_run else ""
        console.print(
            f"{prefix}Undeploy {game}: "
            f"{outcome.links_removed} link(s) removed, "
            f"{outcome.links_skipped} skipped",
        )
        for warning in outcome.warnings:
            console.print(f"WARNING: {warning}")

    _handle_errors(run)


@app.command("remove")
def mod_remove(
    ctx: typer.Context,
    mod: Annotated[str, typer.Argument(help="Mod name or game/name")],
    game: Annotated[
        str | None,
        typer.Option("--game", help="Disambiguate mod name"),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt"),
    ] = False,
    delete_files: Annotated[
        bool,
        typer.Option(
            "--delete-files",
            help="Delete mod files under library_root (requires --yes)",
        ),
    ] = False,
) -> None:
    """Unregister a mod from state and remove its deployed symlinks."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        config_store = _config_store(app_ctx)
        state_store = _state_store(app_ctx)
        config = config_store.load()
        state = state_store.load()
        record = find_mod(state, mod, default_game=game)
        mod_ref = f"{record.game}/{record.name}"
        if delete_files and not yes and not app_ctx.dry_run:
            typer.echo("--delete-files requires --yes", err=True)
            raise typer.Exit(1)
        if not app_ctx.dry_run:
            _confirm_action(
                yes=yes,
                dry_run=False,
                prompt=(
                    f"Remove mod {mod_ref} "
                    f"({len(record.deployed_links)} deployed link(s)"
                    f"{', delete library files' if delete_files else ''})?"
                ),
            )
        updated_state, outcome = remove_mod(
            config,
            state,
            record,
            dry_run=app_ctx.dry_run,
            delete_files=delete_files,
        )
        if not app_ctx.dry_run:
            state_store.save(updated_state)
        if app_ctx.as_json:
            payload = {
                "mod": mod_ref,
                "dry_run": app_ctx.dry_run,
                "links_removed": outcome.links_removed,
                "links_skipped": outcome.links_skipped,
                "deleted_files": outcome.deleted_files,
                "warnings": outcome.warnings,
            }
            typer.echo(json.dumps(payload, indent=2))
            return
        prefix = "[dry-run] " if app_ctx.dry_run else ""
        console.print(
            f"{prefix}Removed mod [bold]{mod_ref}[/bold]: "
            f"{outcome.links_removed} link(s) removed",
        )
        if outcome.deleted_files:
            console.print(f"{prefix}Deleted library files for {mod_ref}")
        for warning in outcome.warnings:
            console.print(f"WARNING: {warning}")

    _handle_errors(run)


@app.command("doctor")
def mod_doctor(ctx: typer.Context) -> None:
    """Validate config, library paths, and mod setup."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        config_store = _config_store(app_ctx)
        state_store = _state_store(app_ctx)
        checks = run_doctor(config_store, state_store)
        if app_ctx.as_json:
            typer.echo(
                json.dumps(
                    [
                        {
                            "name": check.name,
                            "status": check.status,
                            "message": check.message,
                        }
                        for check in checks
                    ],
                    indent=2,
                ),
            )
            if doctor_has_errors(checks):
                raise typer.Exit(1)
            return
        table = Table(title="lmm doctor")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Message")
        for check in checks:
            table.add_row(check.name, check.status, check.message)
        console.print(table)
        if doctor_has_errors(checks):
            raise typer.Exit(1)

    _handle_errors(run)


@app.command("enable")
def mod_enable(
    ctx: typer.Context,
    mod: Annotated[str, typer.Argument(help="Mod name or game/name")],
    game: Annotated[
        str | None,
        typer.Option("--game", help="Disambiguate mod name"),
    ] = None,
) -> None:
    """Enable a mod for deployment."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        state_store = _state_store(app_ctx)
        state = state_store.load()
        updated, record = set_mod_enabled(state, mod, enabled=True, default_game=game)
        state_store.save(updated)
        if app_ctx.as_json:
            typer.echo(
                json.dumps(
                    {
                        "mod": f"{record.game}/{record.name}",
                        "enabled": record.enabled,
                    },
                    indent=2,
                )
            )
            return
        console.print(f"Enabled mod [bold]{record.game}/{record.name}[/bold]")
        _deploy_apply_hint(record.game)

    _handle_errors(run)


@app.command("disable")
def mod_disable(
    ctx: typer.Context,
    mod: Annotated[str, typer.Argument(help="Mod name or game/name")],
    game: Annotated[
        str | None,
        typer.Option("--game", help="Disambiguate mod name"),
    ] = None,
) -> None:
    """Disable a mod for deployment."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        state_store = _state_store(app_ctx)
        state = state_store.load()
        updated, record = set_mod_enabled(state, mod, enabled=False, default_game=game)
        state_store.save(updated)
        if app_ctx.as_json:
            typer.echo(
                json.dumps(
                    {
                        "mod": f"{record.game}/{record.name}",
                        "enabled": record.enabled,
                    },
                    indent=2,
                )
            )
            return
        console.print(f"Disabled mod [bold]{record.game}/{record.name}[/bold]")
        _deploy_apply_hint(record.game)

    _handle_errors(run)


@app.command("identify")
def mod_identify(
    ctx: typer.Context,
    game: Annotated[str, typer.Argument(help="Game profile id")],
) -> None:
    """Identify Nexus metadata for mods in a game via md5 search."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        config_store = _config_store(app_ctx)
        state_store = _state_store(app_ctx)
        config = config_store.load()
        state = state_store.load()
        if app_ctx.dry_run:
            planned = plan_identify(config, state, game)
            if app_ctx.as_json:
                payload = {
                    "dry_run": True,
                    "planned": [
                        {
                            "mod": item.mod_ref,
                            "source_file": str(item.source_file)
                            if item.source_file
                            else None,
                        }
                        for item in planned
                    ],
                }
                typer.echo(json.dumps(payload, indent=2))
                return
            prefix = "[dry-run] "
            console.print(
                f"{prefix}Identify {game}: would query Nexus for {len(planned)} mod(s)",
            )
            for item in planned:
                if item.source_file is None:
                    console.print(f"  {item.mod_ref}: no download file found")
                else:
                    console.print(f"  {item.mod_ref}: {item.source_file}")
            return
        with _nexus_client(app_ctx, config_store) as client:
            client.validate_key()

            def do_identify(
                on_progress: Callable[[str, str], None] | None,
            ) -> tuple:
                return identify_mods(
                    config,
                    state,
                    game,
                    client=client,
                    on_progress=on_progress,
                )

            if app_ctx.as_json:
                updated, identified, failures, skips = do_identify(None)
            else:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=stderr_console,
                    transient=True,
                ) as progress:
                    task_id = progress.add_task("Identifying mods", total=None)

                    def on_progress(mod_ref: str, phase: str) -> None:
                        progress.update(task_id, description=f"{phase} {mod_ref}")

                    updated, identified, failures, skips = do_identify(on_progress)
        state_store.save(updated)
        if app_ctx.as_json:
            payload = {
                "identified": [
                    {
                        "mod": item.mod_ref,
                        "nexus_mod_id": item.nexus_mod_id,
                        "file_id": item.file_id,
                        "installed_version": item.installed_version,
                    }
                    for item in identified
                ],
                "failures": [
                    {"mod": item.mod_ref, "error": item.error} for item in failures
                ],
                "skips": [
                    {"mod": item.mod_ref, "reason": item.reason} for item in skips
                ],
            }
            typer.echo(json.dumps(payload, indent=2))
            if _identify_degraded(updated, game, skips, failures):
                raise typer.Exit(1)
            return
        console.print(
            f"Identify {game}: {len(identified)} mod(s) matched in Nexus",
        )
        for item in skips:
            typer.echo(f"  SKIP {item.mod_ref}: {item.reason}", err=True)
        for item in failures:
            typer.echo(f"  {item.mod_ref}: {item.error}", err=True)
        remaining = unlinked_mods(updated, game)
        if remaining:
            typer.echo(
                f"{len(remaining)} mod(s) still not linked; "
                f"run 'lmm mod link <mod> --url …' or 'lmm mod link <mod> --mod-id N'",
                err=True,
            )
        if _identify_degraded(updated, game, skips, failures):
            raise typer.Exit(1)

    _handle_errors(run)


@app.command("check")
def mod_check(
    ctx: typer.Context,
    game: Annotated[str, typer.Argument(help="Game profile id")],
) -> None:
    """Check installed mods against Nexus latest versions."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        config_store = _config_store(app_ctx)
        state_store = _state_store(app_ctx)
        config = config_store.load()
        state = state_store.load()
        not_linked = unlinked_mods(state, game)
        if not_linked and not app_ctx.dry_run:
            names = ", ".join(f"{mod.game}/{mod.name}" for mod in not_linked)
            typer.echo(
                f"{len(not_linked)} mod(s) not linked to Nexus: {names}. "
                f"Run 'lmm identify {game}' or 'lmm mod link <mod> --url …'.",
                err=True,
            )
        if app_ctx.dry_run:
            planned = plan_check(config, state, game)
            if app_ctx.as_json:
                payload = {
                    "dry_run": True,
                    "planned": [
                        {"mod": item.mod_ref, "reason": item.reason} for item in planned
                    ],
                    "note": ("Live check also queries mods in Nexus mods/updated set"),
                }
                typer.echo(json.dumps(payload, indent=2))
                return
            prefix = "[dry-run] "
            console.print(
                f"{prefix}Check {game}: would query Nexus for "
                f"{len(planned)} stale mod(s)",
            )
            for item in planned:
                console.print(f"  {item.mod_ref}: {item.reason}")
            console.print(
                "[dim]Live check also queries mods in Nexus mods/updated set[/dim]",
            )
            return
        with _nexus_client(app_ctx, config_store) as client:
            client.validate_key()

            def do_check(
                on_progress: Callable[[str, str], None] | None,
            ) -> tuple:
                return check_for_updates(
                    config,
                    state,
                    game,
                    client=client,
                    on_progress=on_progress,
                )

            if app_ctx.as_json:
                updated, updates, failures, version_fallback = do_check(None)
            else:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=stderr_console,
                    transient=True,
                ) as progress:
                    task_id = progress.add_task("Checking mods", total=None)

                    def on_progress(mod_ref: str, phase: str) -> None:
                        progress.update(task_id, description=f"{phase} {mod_ref}")

                    updated, updates, failures, version_fallback = do_check(on_progress)
        state_store.save(updated)
        if app_ctx.as_json:
            payload = {
                "updates": [
                    {
                        "mod": item.mod_ref,
                        "installed_version": item.installed_version,
                        "latest_version": item.latest_version,
                        "non_numeric_versions": item.non_numeric_versions,
                    }
                    for item in updates
                ],
                "failures": [
                    {"mod": item.mod_ref, "error": item.error} for item in failures
                ],
                "version_compare_fallback": version_fallback,
            }
            typer.echo(json.dumps(payload, indent=2))
            if failures:
                raise typer.Exit(1)
            return
        if not updates and not failures:
            console.print(f"Check {game}: no updates found.")
            if version_fallback:
                console.print(
                    "Version comparison used string equality (non-numeric versions).",
                )
            return
        if updates:
            table = Table(title=f"Updates for {game}")
            table.add_column("Mod")
            table.add_column("Installed")
            table.add_column("Latest")
            for item in updates:
                table.add_row(
                    item.mod_ref,
                    item.installed_version or "",
                    item.latest_version,
                )
            console.print(table)
        if version_fallback:
            console.print(
                "Version comparison used string equality (non-numeric versions).",
            )
        for item in failures:
            typer.echo(f"  {item.mod_ref}: {item.error}", err=True)
        if failures:
            raise typer.Exit(1)

    _handle_errors(run)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
