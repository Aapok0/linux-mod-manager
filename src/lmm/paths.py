"""XDG defaults and safe path helpers."""

from __future__ import annotations

import os
from pathlib import Path


class PathValidationError(ValueError):
    """Raised when a path segment or relative path is invalid."""


def _xdg_data_home() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    return Path(data_home) if data_home else Path.home() / ".local" / "share"


def default_library_root() -> Path:
    """Default mod library under the XDG data directory."""
    return _xdg_data_home() / "lmm" / "mods"


def default_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "lmm" / "config.toml"


def default_state_path() -> Path:
    return _xdg_data_home() / "lmm" / "state.json"


def validate_path_segment(name: str, *, field: str) -> str:
    """Reject path segments that could escape or break layout."""
    if not name or name in {".", ".."}:
        msg = f"Invalid {field}: {name!r}"
        raise PathValidationError(msg)
    if "/" in name or "\\" in name:
        msg = f"Invalid {field}: path separators are not allowed in {name!r}"
        raise PathValidationError(msg)
    return name


def validate_relative_subpath(subpath: str, *, field: str) -> str:
    """Reject relative subpaths that escape their root."""
    if not subpath:
        msg = f"Invalid {field}: subpath must not be empty"
        raise PathValidationError(msg)
    path = Path(subpath)
    if path.is_absolute():
        msg = f"Invalid {field}: absolute paths are not allowed ({subpath!r})"
        raise PathValidationError(msg)
    for segment in path.parts:
        validate_path_segment(segment, field=field)
    return subpath


def resolve_under_root(root: Path, *parts: str) -> Path:
    """Join parts under root and ensure the result stays inside root."""
    resolved_root = root.resolve()
    current = resolved_root
    for part in parts:
        for segment in Path(part).parts:
            validate_path_segment(segment, field="path")
            current = current / segment
    resolved = current.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        msg = f"Path escapes root: {resolved} is outside {resolved_root}"
        raise PathValidationError(msg) from exc
    return resolved


def path_within_root(path: Path, root: Path) -> bool:
    """Return True when path resolves inside root."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
