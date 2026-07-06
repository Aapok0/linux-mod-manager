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
from lmm.config import ConfigError, ConfigStore, add_game_profile
from lmm.library import LibraryError, import_mod, list_mods, mod_is_deployed
from lmm.state import StateError, StateStore

app = typer.Typer(
    name="lmm",
    no_args_is_help=True,
    help="Linux Mod Manager (lmm) for Nexus Mods and symlink deployment.",
)
game_app = typer.Typer(help="Manage game profiles.")
app.add_typer(game_app, name="game")

console = Console()

T = TypeVar("T")


@dataclass
class AppContext:
    config_path: Path | None
    state_path: Path | None
    as_json: bool


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
    except (ConfigError, LibraryError, StateError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


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
            targets = "\n".join(str(path) for path in profile.targets)
            table.add_row(
                game_id,
                profile.nexus_domain,
                targets,
                profile.library_subpath or "",
            )
        console.print(table)

    _handle_errors(run)


@app.command("add")
def mod_add(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="Path to mod directory")],
    game: Annotated[str, typer.Option("--game", help="Game profile id")],
    name: Annotated[
        str | None,
        typer.Option("--name", help="Mod name (default: directory name)"),
    ] = None,
    mod_id: Annotated[
        int | None,
        typer.Option("--mod-id", help="Nexus mod id"),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option("--target", help="Per-mod deploy target override"),
    ] = None,
) -> None:
    """Import a mod directory and record it in state."""

    def run() -> None:
        app_ctx = _ctx(ctx)
        config_store = _config_store(app_ctx)
        state_store = _state_store(app_ctx)
        config = config_store.load()
        state = state_store.load()
        target_override: int | str | None
        if target is None:
            target_override = None
        elif target.isdigit():
            target_override = int(target)
        else:
            target_override = target
        updated_state, record = import_mod(
            config,
            state,
            path,
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
