"""Load and save state.json and mod records."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from lmm.io import atomic_write
from lmm.paths import default_state_path

CURRENT_SCHEMA_VERSION = 2


class StateError(Exception):
    """Raised when state cannot be loaded or saved."""


class DeployedLink(BaseModel):
    link: Path
    source: Path

    @field_validator("link", "source", mode="before")
    @classmethod
    def _coerce_path(cls, value: object) -> Path:
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            return Path(value)
        msg = "path fields must be strings or Path objects"
        raise TypeError(msg)


class ModRecord(BaseModel):
    name: str
    game: str
    source_path: Path
    download_path: Path | None = None
    enabled: bool = True
    target: int | str | None = None
    nexus_mod_id: int | None = None
    file_id: int | None = None
    installed_version: str | None = None
    file_md5: str | None = None
    deployed_links: list[DeployedLink] = Field(default_factory=list)
    created_dirs: list[Path] = Field(default_factory=list)
    last_checked: datetime | None = None
    update_available: bool = False
    latest_version: str | None = None
    notes: str | None = None

    @field_validator("source_path", "download_path", mode="before")
    @classmethod
    def _coerce_path_fields(cls, value: object) -> Path | None:
        if value is None:
            return None
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            return Path(value)
        msg = "path fields must be strings or Path objects"
        raise TypeError(msg)

    @field_validator("created_dirs", mode="before")
    @classmethod
    def _coerce_created_dirs(cls, value: object) -> list[Path]:
        if value is None:
            return []
        if not isinstance(value, list):
            msg = "created_dirs must be a list of paths"
            raise TypeError(msg)
        return [item if isinstance(item, Path) else Path(str(item)) for item in value]


class State(BaseModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    mods: list[ModRecord] = Field(default_factory=list)


Migration = Callable[[dict[str, Any]], dict[str, Any]]


def _migrate_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    for mod in raw.get("mods", []):
        if isinstance(mod, dict) and "download_path" not in mod:
            mod["download_path"] = None
    raw["schema_version"] = 2
    return raw


MIGRATIONS: dict[int, Migration] = {1: _migrate_v1_to_v2}


def migrate_state(raw: dict[str, Any]) -> dict[str, Any]:
    version = int(raw.get("schema_version", 0))
    if version > CURRENT_SCHEMA_VERSION:
        msg = f"Unsupported state schema version: {version}"
        raise StateError(msg)
    while version < CURRENT_SCHEMA_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            msg = f"Unsupported state schema version: {version}"
            raise StateError(msg)
        raw = migration(raw)
        version = int(raw.get("schema_version", version + 1))
    raw["schema_version"] = CURRENT_SCHEMA_VERSION
    return raw


class StateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_state_path()

    def load(self) -> State:
        if not self.path.exists():
            return State()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            migrated = migrate_state(raw)
            return State.model_validate(migrated)
        except OSError as exc:
            msg = f"Cannot read state at {self.path}: {exc}"
            raise StateError(msg) from exc
        except JSONDecodeError as exc:
            msg = f"Invalid JSON in state at {self.path}: {exc}"
            raise StateError(msg) from exc
        except ValidationError as exc:
            msg = f"Invalid state at {self.path}: {exc}"
            raise StateError(msg) from exc
        except StateError:
            raise
        except ValueError as exc:
            msg = f"Invalid state at {self.path}: {exc}"
            raise StateError(msg) from exc

    def save(self, state: State) -> None:
        payload = state.model_dump(mode="json")
        content = json.dumps(payload, indent=2) + "\n"
        try:
            atomic_write(self.path, content)
        except OSError as exc:
            msg = f"Cannot write state to {self.path}: {exc}"
            raise StateError(msg) from exc


def find_mod(
    state: State, reference: str, *, default_game: str | None = None
) -> ModRecord:
    if "/" in reference:
        game, name = reference.split("/", 1)
        for mod in state.mods:
            if mod.game == game and mod.name == name:
                return mod
        msg = f"Mod not found: {reference}"
        raise StateError(msg)

    matches = [mod for mod in state.mods if mod.name == reference]
    if default_game is not None:
        matches = [mod for mod in matches if mod.game == default_game]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        msg = f"Mod not found: {reference}"
        raise StateError(msg)
    refs = ", ".join(f"{mod.game}/{mod.name}" for mod in matches)
    msg = (
        f"Ambiguous mod reference: {reference} (matches: {refs}). "
        "Use --game to disambiguate."
    )
    raise StateError(msg)


def remove_mod_record(state: State, game: str, name: str) -> State:
    updated = state.model_copy(deep=True)
    remaining = [
        mod for mod in updated.mods if not (mod.game == game and mod.name == name)
    ]
    if len(remaining) == len(updated.mods):
        msg = f"Mod not found: {game}/{name}"
        raise StateError(msg)
    updated.mods = remaining
    return updated


def add_mod_record(state: State, record: ModRecord) -> State:
    for mod in state.mods:
        if mod.game == record.game and mod.name == record.name:
            msg = f"Mod already exists: {record.game}/{record.name}"
            raise ValueError(msg)
    updated = state.model_copy(deep=True)
    updated.mods.append(record)
    return updated


def update_mod_record(state: State, record: ModRecord) -> State:
    updated = state.model_copy(deep=True)
    for index, mod in enumerate(updated.mods):
        if mod.game == record.game and mod.name == record.name:
            updated.mods[index] = record
            return updated
    msg = f"Mod not found: {record.game}/{record.name}"
    raise StateError(msg)


def mods_referencing_target_index(
    state: State, game_id: str, index: int
) -> list[ModRecord]:
    return [mod for mod in state.mods if mod.game == game_id and mod.target == index]


def adjust_mod_targets_after_remove(
    state: State, game_id: str, removed_index: int
) -> State:
    updated = state.model_copy(deep=True)
    for idx, mod in enumerate(updated.mods):
        if mod.game != game_id or not isinstance(mod.target, int):
            continue
        if mod.target > removed_index:
            updated.mods[idx] = mod.model_copy(update={"target": mod.target - 1})
    return updated


def set_mod_enabled(
    state: State,
    reference: str,
    *,
    enabled: bool,
    default_game: str | None = None,
) -> tuple[State, ModRecord]:
    mod = find_mod(state, reference, default_game=default_game)
    updated = state.model_copy(deep=True)
    for index, entry in enumerate(updated.mods):
        if entry.game == mod.game and entry.name == mod.name:
            updated_mod = entry.model_copy(update={"enabled": enabled})
            updated.mods[index] = updated_mod
            return updated, updated_mod
    msg = f"Mod not found: {reference}"
    raise StateError(msg)
