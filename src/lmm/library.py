"""Import mods into the library and list registered mods."""

from __future__ import annotations

import enum
import shutil
from dataclasses import dataclass
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


class ImportAction(enum.StrEnum):
    REGISTERED = "registered"
    COPIED = "copied"
    MOVED = "moved"


@dataclass
class ImportSkip:
    path: Path
    reason: str


@dataclass
class ImportFailure:
    name: str
    error: str


@dataclass
class ImportResult:
    record: ModRecord
    action: ImportAction


def _mod_registered(state: State, game_id: str, name: str) -> bool:
    return any(mod.game == game_id and mod.name == name for mod in state.mods)


def discover_mod_dirs(parent: Path) -> list[Path]:
    """Return sorted immediate child directories, excluding dot-prefixed names."""
    parent = parent.resolve()
    return sorted(
        entry
        for entry in parent.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    )


def _planned_import(
    config: Config,
    source: Path,
    game_id: str,
    *,
    copy: bool,
) -> tuple[ModRecord, ImportAction]:
    mod_name = validate_path_segment(source.name, field="mod name")
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
        action = ImportAction.REGISTERED
    else:
        destination = resolve_mod_destination(config, game_id, mod_name)
        if destination.exists():
            msg = f"Destination already exists: {destination}"
            raise LibraryError(msg)
        action = ImportAction.MOVED if not copy else ImportAction.COPIED

    record = ModRecord(
        name=mod_name,
        game=game_id,
        source_path=destination,
    )
    return record, action


def import_mods_from_directory(
    config: Config,
    state: State,
    parent: Path,
    game_id: str,
    *,
    copy: bool = True,
    dry_run: bool = False,
) -> tuple[State, list[ImportResult], list[ImportFailure], list[ImportSkip]]:
    if game_id not in config.games:
        msg = f"Unknown game profile: {game_id}"
        raise LibraryError(msg)

    parent = parent.resolve()
    if not parent.exists():
        msg = f"Mod path does not exist: {parent}"
        raise LibraryError(msg)
    if not parent.is_dir():
        msg = f"Mod path is not a directory: {parent}"
        raise LibraryError(msg)

    updated = state.model_copy(deep=True)
    results: list[ImportResult] = []
    failures: list[ImportFailure] = []
    skips: list[ImportSkip] = []

    for entry in sorted(parent.iterdir(), key=lambda path: path.name):
        if entry.is_file():
            skips.append(ImportSkip(path=entry, reason="not_a_directory"))
            continue
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            skips.append(ImportSkip(path=entry, reason="hidden"))
            continue

        mod_name = entry.name
        if _mod_registered(updated, game_id, mod_name):
            skips.append(ImportSkip(path=entry, reason="already_registered"))
            continue

        if dry_run:
            try:
                record, action = _planned_import(config, entry, game_id, copy=copy)
            except (LibraryError, PathValidationError, ValueError) as exc:
                failures.append(ImportFailure(name=mod_name, error=str(exc)))
                continue
            results.append(ImportResult(record=record, action=action))
            continue

        try:
            updated, record, action = import_mod(
                config,
                updated,
                entry,
                game_id=game_id,
                copy=copy,
            )
        except (LibraryError, ValueError) as exc:
            failures.append(ImportFailure(name=mod_name, error=str(exc)))
            continue
        results.append(ImportResult(record=record, action=action))

    if dry_run:
        return state, results, failures, skips
    return updated, results, failures, skips


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
) -> tuple[State, ModRecord, ImportAction]:
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
            action = ImportAction.REGISTERED
        else:
            destination = resolve_mod_destination(config, game_id, mod_name)
            if destination.exists():
                msg = f"Destination already exists: {destination}"
                raise LibraryError(msg)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if copy:
                shutil.copytree(source, destination)
                action = ImportAction.COPIED
            else:
                shutil.move(str(source), str(destination))
                action = ImportAction.MOVED
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
    return updated_state, record, action


def list_mods(state: State, game_id: str | None = None) -> list[ModRecord]:
    if game_id is None:
        mods = list(state.mods)
    else:
        mods = [mod for mod in state.mods if mod.game == game_id]
    return sorted(mods, key=lambda mod: (mod.game, mod.name))


def mod_is_deployed(mod: ModRecord) -> bool:
    return len(mod.deployed_links) > 0
