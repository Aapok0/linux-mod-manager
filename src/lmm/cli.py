"""Typer CLI for lmm."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypeVar

import typer
from rich.console import Console
from rich.table import Table

from lmm import __version__
from lmm.config import (
    ConfigError,
    ConfigStore,
    add_game_profile,
    add_game_target,
    remove_game_target,
)
from lmm.deploy import DeployError, deploy_game, undeploy_game
from lmm.library import (
    LibraryError,
    import_mod,
    list_mods,
    mod_is_deployed,
    resolve_mod_source,
)
from lmm.nexus import NexusClient, NexusError
from lmm.nexus.updates import check_for_updates, identify_mods
from lmm.state import (
    StateError,
    StateStore,
    adjust_mod_targets_after_remove,
    mods_referencing_target_index,
    set_mod_enabled,
)

app = typer.Typer(
    name="lmm",
    no_args_is_help=True,
    help="Linux Mod Manager (lmm) for Nexus Mods and symlink deployment.",
)
game_app = typer.Typer(help="Manage game profiles.")
app.add_typer(game_app, name="game")
target_app = typer.Typer(help="Manage deploy targets for a game profile.")
game_app.add_typer(target_app, name="target")

console = Console()

T = TypeVar("T")


@dataclass
class AppContext:
    config_path: Path | None
    state_path: Path | None
    as_json: bool
    dry_run: bool


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
    version: Annotated[
        bool,
        typer.Option("--version", help="Show version and exit"),
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    ctx.obj = AppContext(
        config_path=config,
        state_path=state,
        as_json=as_json,
        dry_run=dry_run,
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
            help="Subpath under library_root for this game's mods",
        ),
    ] = None,
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
        table.add_column("Targets")
        table.add_column("Library subpath")
        for game_id, profile in sorted(config.games.items()):
            targets = "\n".join(
                f"[{index}] {path}" for index, path in enumerate(profile.targets)
            )
            table.add_row(
                game_id,
                profile.nexus_domain,
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

        for target_index in sorted(set(index), reverse=True):
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
            help="Mod directory path or bare mod name under the game library"
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
    target_index: Annotated[
        int | None,
        typer.Option("--target-index", help="Deploy target index override"),
    ] = None,
    target_path: Annotated[
        Path | None,
        typer.Option("--target-path", help="Absolute deploy target path override"),
    ] = None,
) -> None:
    """Import a mod directory and record it in state."""
    if target_index is not None and target_path is not None:
        raise typer.BadParameter("Use only one of --target-index or --target-path")

    def run() -> None:
        app_ctx = _ctx(ctx)
        config_store = _config_store(app_ctx)
        state_store = _state_store(app_ctx)
        config = config_store.load()
        state = state_store.load()
        source = resolve_mod_source(config, game, name_or_path)
        target_override: int | str | None
        if target_index is not None:
            target_override = target_index
        elif target_path is not None:
            target_override = str(target_path)
        else:
            target_override = None
        updated_state, record = import_mod(
            config,
            state,
            source,
            game_id=game,
            name=name,
            nexus_mod_id=mod_id,
            target=target_override,
        )
        state_store.save(updated_state)
        if app_ctx.as_json:
            typer.echo(record.model_dump_json(indent=2))
            return
        console.print(
            f"Added mod [bold]{record.game}/{record.name}[/bold] "
            f"at {record.source_path}",
        )

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
        typer.Option("--game", help="Filter by game profile id"),
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
        table = Table(title="Mods")
        table.add_column("Game")
        table.add_column("Name")
        table.add_column("Version")
        table.add_column("Enabled")
        table.add_column("Deployed")
        table.add_column("Source")
        for mod in mods:
            table.add_row(
                mod.game,
                mod.name,
                mod.installed_version or "",
                "yes" if mod.enabled else "no",
                "yes" if mod_is_deployed(mod) else "no",
                str(mod.source_path),
            )
        console.print(table)

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
        prefix = "[dry-run] " if app_ctx.dry_run else ""
        console.print(
            f"{prefix}Deploy {game}: "
            f"{outcome.links_created} link(s) created, "
            f"{outcome.links_removed} removed, "
            f"{outcome.links_skipped} skipped",
        )
        for conflict in outcome.conflicts:
            console.print(f"[yellow]Conflict:[/yellow] {conflict}")
        for warning in outcome.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")

    _handle_errors(run)


@app.command("undeploy")
def mod_undeploy(
    ctx: typer.Context,
    game: Annotated[str, typer.Argument(help="Game profile id")],
) -> None:
    """Remove deployed symlinks recorded for a game."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        config = _config_store(app_ctx).load()
        state_store = _state_store(app_ctx)
        state = state_store.load()
        updated_state, outcome = undeploy_game(
            config,
            state,
            game,
            dry_run=app_ctx.dry_run,
        )
        if not app_ctx.dry_run:
            state_store.save(updated_state)
        prefix = "[dry-run] " if app_ctx.dry_run else ""
        console.print(
            f"{prefix}Undeploy {game}: "
            f"{outcome.links_removed} link(s) removed, "
            f"{outcome.links_skipped} skipped",
        )
        for warning in outcome.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")

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
        console.print(f"Enabled mod [bold]{record.game}/{record.name}[/bold]")

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
        console.print(f"Disabled mod [bold]{record.game}/{record.name}[/bold]")

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
        with NexusClient(api_key=config_store.resolve_api_key(config)) as client:
            client.validate_key()
            updated, identified = identify_mods(config, state, game, client=client)
        state_store.save(updated)
        if app_ctx.as_json:
            payload = [
                {
                    "mod": item.mod_ref,
                    "nexus_mod_id": item.nexus_mod_id,
                    "file_id": item.file_id,
                    "installed_version": item.installed_version,
                }
                for item in identified
            ]
            typer.echo(json.dumps(payload, indent=2))
            return
        console.print(
            f"Identify {game}: {len(identified)} mod(s) matched in Nexus",
        )

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
        with NexusClient(api_key=config_store.resolve_api_key(config)) as client:
            client.validate_key()
            updated, updates = check_for_updates(config, state, game, client=client)
        state_store.save(updated)
        if app_ctx.as_json:
            payload = [
                {
                    "mod": item.mod_ref,
                    "installed_version": item.installed_version,
                    "latest_version": item.latest_version,
                }
                for item in updates
            ]
            typer.echo(json.dumps(payload, indent=2))
            return
        if not updates:
            console.print(f"Check {game}: no updates found.")
            return
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

    _handle_errors(run)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
