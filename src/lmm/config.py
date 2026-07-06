"""Load and save config.toml and game profiles."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, Field, ValidationError, field_validator

from lmm.io import atomic_write
from lmm.paths import (
    PathValidationError,
    default_config_path,
    default_library_root,
    validate_path_segment,
    validate_relative_subpath,
)

CURRENT_SCHEMA_VERSION = 1
DeployMethod = Literal["symlink"]


class ConfigError(Exception):
    """Raised when config cannot be loaded or saved."""


class GameProfile(BaseModel):
    nexus_domain: str
    targets: list[Path]
    deploy_method: DeployMethod = "symlink"
    library_subpath: str | None = None

    @field_validator("targets", mode="before")
    @classmethod
    def _coerce_targets(cls, value: object) -> list[Path]:
        if not isinstance(value, list):
            msg = "targets must be a list of paths"
            raise TypeError(msg)
        return [item if isinstance(item, Path) else Path(str(item)) for item in value]

    @field_validator("library_subpath", mode="before")
    @classmethod
    def _validate_library_subpath(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            msg = "library_subpath must be a string"
            raise TypeError(msg)
        return validate_relative_subpath(value, field="library_subpath")


class Config(BaseModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    library_root: Path = Field(default_factory=default_library_root)
    nexus_api_key: str = ""
    games: dict[str, GameProfile] = Field(default_factory=dict)

    @field_validator("library_root", mode="before")
    @classmethod
    def _coerce_library_root(cls, value: object) -> Path:
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            return Path(value)
        msg = "library_root must be a path"
        raise TypeError(msg)


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_path()

    def load(self) -> Config:
        if not self.path.exists():
            return Config()
        try:
            raw = tomllib.loads(self.path.read_text(encoding="utf-8"))
            return Config.model_validate(raw)
        except OSError as exc:
            msg = f"Cannot read config at {self.path}: {exc}"
            raise ConfigError(msg) from exc
        except tomllib.TOMLDecodeError as exc:
            msg = f"Invalid TOML in config at {self.path}: {exc}"
            raise ConfigError(msg) from exc
        except ValidationError as exc:
            msg = f"Invalid config at {self.path}: {exc}"
            raise ConfigError(msg) from exc

    def save(self, config: Config) -> None:
        payload = config.model_dump(mode="python")
        content = tomli_w.dumps(_config_to_toml(payload))
        try:
            atomic_write(self.path, content)
        except OSError as exc:
            msg = f"Cannot write config to {self.path}: {exc}"
            raise ConfigError(msg) from exc

    def resolve_api_key(self, config: Config) -> str | None:
        env_key = os.environ.get("NEXUS_API_KEY", "").strip()
        if env_key:
            return env_key
        file_key = config.nexus_api_key.strip()
        return file_key or None


def _config_to_toml(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": payload["schema_version"],
        "library_root": str(payload["library_root"]),
        "nexus_api_key": payload["nexus_api_key"],
    }
    games: dict[str, Any] = {}
    for game_id, profile in payload["games"].items():
        games[game_id] = {
            "nexus_domain": profile["nexus_domain"],
            "targets": [str(path) for path in profile["targets"]],
            "deploy_method": profile["deploy_method"],
        }
        if profile.get("library_subpath"):
            games[game_id]["library_subpath"] = profile["library_subpath"]
    if games:
        result["games"] = games
    return result


def add_game_profile(
    config: Config,
    game_id: str,
    *,
    nexus_domain: str,
    targets: list[Path],
    deploy_method: DeployMethod = "symlink",
    library_subpath: str | None = None,
) -> Config:
    if game_id in config.games:
        msg = f"Game profile already exists: {game_id}"
        raise ValueError(msg)
    try:
        validate_path_segment(game_id, field="game id")
        if library_subpath is not None:
            validate_relative_subpath(library_subpath, field="library_subpath")
    except PathValidationError as exc:
        raise ValueError(str(exc)) from exc
    updated = config.model_copy(deep=True)
    updated.games[game_id] = GameProfile(
        nexus_domain=nexus_domain,
        targets=targets,
        deploy_method=deploy_method,
        library_subpath=library_subpath,
    )
    return updated
