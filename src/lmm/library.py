"""Import mods into the library and list registered mods."""

from __future__ import annotations

import shutil
from pathlib import Path

from lmm.config import Config
from lmm.paths import (
    PathValidationError,
    path_within_root,
    resolve_under_root,
    validate_path_segment,
)
from lmm.state import ModRecord, State, add_mod_record


class LibraryError(Exception):
    """Raised when a library operation fails."""


def game_library_dir(config: Config, game_id: str) -> Path:
    profile = config.games.get(game_id)
    if profile is None:
        msg = f"Unknown game profile: {game_id}"
        raise LibraryError(msg)
    if profile.library_subpath:
        return resolve_under_root(
            config.library_root,
            profile.library_subpath,
        )
    return resolve_under_root(config.library_root, game_id)


def resolve_mod_destination(
    config: Config,
    game_id: str,
    name: str,
) -> Path:
    validated_name = validate_path_segment(name, field="mod name")
    return resolve_under_root(game_library_dir(config, game_id), validated_name)


def resolve_mod_source(config: Config, game_id: str, name_or_path: Path) -> Path:
    """Resolve bare mod name to game_library_dir/name when that directory exists."""
    arg = str(name_or_path)
    if (
        not name_or_path.is_absolute()
        and "/" not in arg
        and "\\" not in arg
        and game_id in config.games
    ):
        try:
            candidate = resolve_mod_destination(config, game_id, arg)
            if candidate.is_dir():
                return candidate.resolve()
        except PathValidationError as exc:
            raise LibraryError(str(exc)) from exc
    return name_or_path.resolve()


def import_mod(
    config: Config,
    state: State,
    source: Path,
    *,
    game_id: str,
    name: str | None = None,
    nexus_mod_id: int | None = None,
    target: int | str | None = None,
    copy: bool = True,
) -> tuple[State, ModRecord]:
    if game_id not in config.games:
        msg = f"Unknown game profile: {game_id}"
        raise LibraryError(msg)

    source = source.resolve()
    if not source.exists():
        msg = f"Mod path does not exist: {source}"
        raise LibraryError(msg)
    if not source.is_dir():
        msg = f"Mod path is not a directory: {source}"
        raise LibraryError(msg)

    try:
        mod_name = validate_path_segment(name or source.name, field="mod name")
        library_root = config.library_root.resolve()
        game_dir = game_library_dir(config, game_id)

        if path_within_root(source, library_root):
            if not path_within_root(source, game_dir):
                msg = (
                    f"Mod path is in the library but not under this game's directory: "
                    f"{source} (expected under {game_dir})"
                )
                raise LibraryError(msg)
            destination = source
        else:
            destination = resolve_mod_destination(config, game_id, mod_name)
            if destination.exists():
                msg = f"Destination already exists: {destination}"
                raise LibraryError(msg)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if copy:
                shutil.copytree(source, destination)
            else:
                shutil.move(str(source), str(destination))
    except PathValidationError as exc:
        raise LibraryError(str(exc)) from exc

    record = ModRecord(
        name=mod_name,
        game=game_id,
        source_path=destination,
        nexus_mod_id=nexus_mod_id,
        target=target,
    )
    updated_state = add_mod_record(state, record)
    return updated_state, record


def list_mods(state: State, game_id: str | None = None) -> list[ModRecord]:
    if game_id is None:
        mods = list(state.mods)
    else:
        mods = [mod for mod in state.mods if mod.game == game_id]
    return sorted(mods, key=lambda mod: (mod.game, mod.name))


def mod_is_deployed(mod: ModRecord) -> bool:
    return len(mod.deployed_links) > 0
