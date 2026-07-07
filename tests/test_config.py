"""Tests for config load/save and API key resolution."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from lmm.config import (
    Config,
    ConfigStore,
    GameProfile,
    add_game_profile,
    add_game_target,
)


def test_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    store = ConfigStore(path)
    config = Config(
        library_root=tmp_path / "mods",
        nexus_api_key="file-key",
        games={
            "kcd2": GameProfile(
                nexus_domain="kingdomcomedeliverance2",
                targets=[Path("/tmp/game/Mods")],
                library_subpath="KingdomComeDeliverance2/Mods",
            )
        },
    )
    store.save(config)
    loaded = store.load()
    assert loaded.library_root == config.library_root
    assert loaded.nexus_api_key == "file-key"
    assert loaded.games["kcd2"].nexus_domain == "kingdomcomedeliverance2"
    assert loaded.games["kcd2"].targets == [Path("/tmp/game/Mods")]
    assert loaded.games["kcd2"].library_subpath == "KingdomComeDeliverance2/Mods"


def test_api_key_prefers_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    config = Config(nexus_api_key="file-key")
    monkeypatch.setenv("NEXUS_API_KEY", "env-key")
    assert store.resolve_api_key(config) == "env-key"


def test_api_key_falls_back_to_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    config = Config(nexus_api_key="file-key")
    monkeypatch.delenv("NEXUS_API_KEY", raising=False)
    assert store.resolve_api_key(config) == "file-key"


def test_add_game_profile_rejects_duplicate() -> None:
    config = Config(
        games={
            "kcd2": GameProfile(
                nexus_domain="kingdomcomedeliverance2",
                targets=[Path("/tmp/game/Mods")],
            )
        }
    )
    with pytest.raises(ValueError, match="already exists"):
        add_game_profile(
            config,
            "kcd2",
            nexus_domain="kingdomcomedeliverance2",
            targets=[Path("/other")],
        )


def test_saved_toml_is_readable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    store = ConfigStore(path)
    store.save(
        add_game_profile(
            Config(library_root=tmp_path / "library"),
            "kcd2",
            nexus_domain="kingdomcomedeliverance2",
            targets=[Path("/tmp/game/Mods")],
        )
    )
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["games"]["kcd2"]["nexus_domain"] == "kingdomcomedeliverance2"


def test_add_game_target_round_trip(tmp_path: Path) -> None:
    primary = tmp_path / "game" / "Mods"
    secondary = tmp_path / "game" / "Data"
    config = add_game_profile(
        Config(library_root=tmp_path / "library"),
        "kcd2",
        nexus_domain="kingdomcomedeliverance2",
        targets=[primary],
    )
    updated = add_game_target(config, "kcd2", secondary)
    path = tmp_path / "config.toml"
    store = ConfigStore(path)
    store.save(updated)
    loaded = store.load()
    assert loaded.games["kcd2"].targets == [primary, secondary]
