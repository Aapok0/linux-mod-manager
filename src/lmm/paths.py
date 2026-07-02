"""XDG and default path helpers."""

from __future__ import annotations

import os
from pathlib import Path


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
