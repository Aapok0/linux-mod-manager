"""Tests for XDG default paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from lmm.config import Config
from lmm.paths import default_library_root


def test_default_library_root_uses_xdg_data_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert default_library_root() == tmp_path / "data" / "lmm" / "mods"


def test_config_default_library_root_is_overridable(tmp_path: Path) -> None:
    custom = tmp_path / "Games" / "StowMods" / "Mods"
    config = Config(library_root=custom)
    assert config.library_root == custom
