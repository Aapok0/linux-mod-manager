"""Load and save state.json and mod records."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from lmm.paths import default_state_path

CURRENT_SCHEMA_VERSION = 1


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
    enabled: bool = True
    target: int | str | None = None
    nexus_mod_id: int | None = None
    file_id: int | None = None
    installed_version: str | None = None
    file_md5: str | None = None
    deployed_links: list[DeployedLink] = Field(default_factory=list)
    last_checked: datetime | None = None
    update_available: bool = False
    latest_version: str | None = None

    @field_validator("source_path", mode="before")
    @classmethod
    def _coerce_source_path(cls, value: object) -> Path:
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            return Path(value)
        msg = "source_path must be a path"
        raise TypeError(msg)


class State(BaseModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    mods: list[ModRecord] = Field(default_factory=list)


Migration = Callable[[dict[str, Any]], dict[str, Any]]
MIGRATIONS: dict[int, Migration] = {}


def migrate_state(raw: dict[str, Any]) -> dict[str, Any]:
    version = int(raw.get("schema_version", 0))
    while version < CURRENT_SCHEMA_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            msg = f"Unsupported state schema version: {version}"
            raise ValueError(msg)
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
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        migrated = migrate_state(raw)
        return State.model_validate(migrated)

    def save(self, state: State) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = state.model_dump(mode="json")
        self.path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )


def find_mod(
    state: State, reference: str, *, default_game: str | None = None
) -> ModRecord:
    if "/" in reference:
        game, name = reference.split("/", 1)
        for mod in state.mods:
            if mod.game == game and mod.name == name:
                return mod
        msg = f"Mod not found: {reference}"
        raise KeyError(msg)

    matches = [mod for mod in state.mods if mod.name == reference]
    if default_game is not None:
        matches = [mod for mod in matches if mod.game == default_game]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        msg = f"Mod not found: {reference}"
        raise KeyError(msg)
    msg = f"Ambiguous mod reference: {reference}"
    raise KeyError(msg)


def add_mod_record(state: State, record: ModRecord) -> State:
    for mod in state.mods:
        if mod.game == record.game and mod.name == record.name:
            msg = f"Mod already exists: {record.game}/{record.name}"
            raise ValueError(msg)
    updated = state.model_copy(deep=True)
    updated.mods.append(record)
    return updated
