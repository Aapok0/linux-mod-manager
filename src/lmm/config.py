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


def _format_config_validation_error(exc: ValidationError) -> str:
    if not exc.errors():
        return "Invalid config"
    first = exc.errors()[0]
    loc = first.get("loc", ())
    msg = str(first.get("msg", "validation error"))
    if len(loc) >= 2 and loc[0] == "games" and isinstance(loc[1], str):
        game_id = loc[1]
        field = loc[2] if len(loc) > 2 else None
        if field == "targets":
            return (
                f"Game '{game_id}' is missing deploy targets in config.toml "
                f"(add at least one --target on 'lmm game add')"
            )
        if field:
            return f"Game '{game_id}' field '{field}': {msg}"
        return f"Game '{game_id}': {msg}"
    if loc == ("library_root",):
        return f"library_root in config.toml: {msg}"
    location = ".".join(str(part) for part in loc) if loc else "config"
    return f"Invalid config at {location}: {msg}"


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_path()

    def load(self) -> Config:
        if not self.path.exists():
            config = Config()
        else:
            try:
                raw = tomllib.loads(self.path.read_text(encoding="utf-8"))
                config = Config.model_validate(raw)
            except OSError as exc:
                msg = f"Cannot read config at {self.path}: {exc}"
                raise ConfigError(msg) from exc
            except tomllib.TOMLDecodeError as exc:
                msg = f"Invalid TOML in config at {self.path}: {exc}"
                raise ConfigError(msg) from exc
            except ValidationError as exc:
                detail = _format_config_validation_error(exc)
                msg = f"Invalid config at {self.path}: {detail}"
                raise ConfigError(msg) from exc
        return self._apply_env_overrides(config)

    def _apply_env_overrides(self, config: Config) -> Config:
        env_root = os.environ.get("LMM_LIBRARY_ROOT", "").strip()
        if env_root:
            return config.model_copy(update={"library_root": Path(env_root)})
        return config

    def resolve_library_root(self, config: Config) -> Path:
        env_root = os.environ.get("LMM_LIBRARY_ROOT", "").strip()
        if env_root:
            return Path(env_root)
        return config.library_root

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


def _require_game_profile(config: Config, game_id: str) -> GameProfile:
    profile = config.games.get(game_id)
    if profile is None:
        msg = f"Unknown game profile: {game_id}"
        raise ValueError(msg)
    return profile


def add_game_target(config: Config, game_id: str, target: Path) -> Config:
    profile = _require_game_profile(config, game_id)
    resolved = target.resolve()
    for existing in profile.targets:
        if existing.resolve() == resolved:
            msg = f"Deploy target already configured: {target}"
            raise ValueError(msg)
    updated = config.model_copy(deep=True)
    updated.games[game_id].targets.append(target)
    return updated


def remove_game_target(config: Config, game_id: str, index: int) -> Config:
    profile = _require_game_profile(config, game_id)
    if index == 0:
        msg = "Cannot remove primary deploy target (index 0)"
        raise ValueError(msg)
    if len(profile.targets) == 1:
        msg = f"Game profile {game_id} must keep at least one deploy target"
        raise ValueError(msg)
    if index < 0 or index >= len(profile.targets):
        msg = (
            f"Deploy target index {index} out of range for game "
            f"{game_id} (0-{len(profile.targets) - 1})"
        )
        raise ValueError(msg)
    updated = config.model_copy(deep=True)
    del updated.games[game_id].targets[index]
    return updated
