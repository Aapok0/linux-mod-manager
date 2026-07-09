"""Tests for Nexus identify/check workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from lmm.config import Config, add_game_profile
from lmm.library import import_mod
from lmm.nexus.updates import check_for_updates, identify_mods, is_newer_version
from lmm.state import State


class FakeClient:
    def __init__(self) -> None:
        self.md5_payload = [{"mod_id": 42, "file_id": 7, "version": "1.1.0"}]
        self.updated_payload = [{"mod_id": 42}]
        self.files_payload = [
            {
                "file_id": 7,
                "version": "1.2.0",
                "category_name": "MAIN",
                "uploaded_timestamp": 1000,
            }
        ]

    def md5_search(self, _: str, __: str) -> list[dict]:
        return self.md5_payload

    def updated_mods(self, _: str, *, period: str = "1w") -> list[dict]:
        assert period == "1w"
        return self.updated_payload

    def mod_files(self, _: str, __: int) -> list[dict]:
        return self.files_payload


def _setup_mod(tmp_path: Path) -> tuple[Config, State]:
    library_root = tmp_path / "library"
    library_root.mkdir()
    game_target = tmp_path / "game" / "Mods"
    game_target.mkdir(parents=True)
    config = add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kingdomcomedeliverance2",
        targets=[game_target],
        library_subpath="KCD2/Mods",
    )
    source = tmp_path / "incoming" / "moda"
    source.mkdir(parents=True)
    (source / "file.txt").write_text("abc", encoding="utf-8")
    state, _ = import_mod(config, State(), source, game_id="kcd2")
    return config, state


def test_identify_mods_updates_state(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    updated, results = identify_mods(config, state, "kcd2", client=FakeClient())
    assert len(results) == 1
    mod = updated.mods[0]
    assert mod.nexus_mod_id == 42
    assert mod.file_id == 7
    assert mod.installed_version == "1.1.0"
    assert mod.file_md5


def test_check_for_updates_marks_update_available(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    seeded = state.model_copy(deep=True)
    seeded.mods[0] = seeded.mods[0].model_copy(
        update={
            "nexus_mod_id": 42,
            "installed_version": "1.0.0",
            "last_checked": datetime(2020, 1, 1, tzinfo=UTC),
        }
    )
    updated, results = check_for_updates(config, seeded, "kcd2", client=FakeClient())
    assert len(results) == 1
    assert results[0].latest_version == "1.2.0"
    assert updated.mods[0].update_available is True
    assert updated.mods[0].latest_version == "1.2.0"


def test_is_newer_version_semver_and_fallback() -> None:
    assert is_newer_version("1.0.0", "1.0.1") is True
    assert is_newer_version("1.0.1", "1.0.0") is False
    assert is_newer_version("abc", "def") is True
    assert is_newer_version("abc", "abc") is False
