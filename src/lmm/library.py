"""Import mods into the library and list registered mods."""

from __future__ import annotations

import shutil
from pathlib import Path

from lmm.config import Config
from lmm.state import ModRecord, State, add_mod_record


class LibraryError(Exception):
    """Raised when a library operation fails."""


def game_library_dir(config: Config, game_id: str) -> Path:
    profile = config.games.get(game_id)
    if profile is None:
        msg = f"Unknown game profile: {game_id}"
        raise LibraryError(msg)
    if profile.library_subpath:
        return config.library_root / profile.library_subpath
    return config.library_root / game_id


def resolve_mod_destination(
    config: Config,
    game_id: str,
    name: str,
) -> Path:
    return game_library_dir(config, game_id) / name


def _path_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


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
) -> tuple[Config, State, ModRecord]:
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

    mod_name = name or source.name
    library_root = config.library_root.resolve()

    if _path_within_root(source, library_root):
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

    record = ModRecord(
        name=mod_name,
        game=game_id,
        source_path=destination,
        nexus_mod_id=nexus_mod_id,
        target=target,
    )
    updated_state = add_mod_record(state, record)
    return config, updated_state, record


def list_mods(state: State, game_id: str | None = None) -> list[ModRecord]:
    if game_id is None:
        return list(state.mods)
    return [mod for mod in state.mods if mod.game == game_id]


def mod_is_deployed(mod: ModRecord) -> bool:
    return len(mod.deployed_links) > 0
