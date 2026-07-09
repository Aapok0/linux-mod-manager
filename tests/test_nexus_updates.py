"""Tests for Nexus identify/check workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lmm.config import Config, ConfigError, add_game_profile
from lmm.library import import_mod
from lmm.nexus import NexusError
from lmm.nexus.updates import check_for_updates, identify_mods, is_newer_version
from lmm.state import State


class FakeClient:
    def __init__(
        self,
        *,
        fail_first_md5: bool = False,
        fail_mod_files_for: set[int] | None = None,
    ) -> None:
        self.md5_payload = [{"mod_id": 42, "file_id": 7, "version": "1.1.0"}]
        self.updated_payload = [{"mod_id": 42}, {"mod_id": 43}]
        self.files_payload = [
            {
                "file_id": 7,
                "version": "1.2.0",
                "category_name": "MAIN",
                "uploaded_timestamp": 1000,
            }
        ]
        self.fail_first_md5 = fail_first_md5
        self.fail_mod_files_for = fail_mod_files_for or set()
        self.md5_calls = 0

    def md5_search(self, _: str, md5_hash: str) -> list[dict]:
        self.md5_calls += 1
        if self.fail_first_md5 and self.md5_calls == 1:
            msg = f"md5_search failed for {md5_hash}"
            raise NexusError(msg)
        mod_id = 42 if self.md5_calls == 1 else 43
        return [{"mod_id": mod_id, "file_id": 7, "version": "1.1.0"}]

    def updated_mods(self, _: str, *, period: str = "1w") -> list[dict]:
        assert period == "1w"
        return self.updated_payload

    def mod_files(self, _: str, mod_id: int) -> list[dict]:
        if mod_id in self.fail_mod_files_for:
            msg = f"mod_files failed for {mod_id}"
            raise NexusError(msg)
        version = "1.2.0" if mod_id == 42 else "2.0.0"
        return [
            {
                "file_id": 7,
                "version": version,
                "category_name": "MAIN",
                "uploaded_timestamp": 1000,
            }
        ]


def _setup_mod(tmp_path: Path, name: str = "moda") -> tuple[Config, State]:
    library_root = tmp_path / "library"
    library_root.mkdir(exist_ok=True)
    game_target = tmp_path / "game" / "Mods"
    game_target.mkdir(parents=True, exist_ok=True)
    config = add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kingdomcomedeliverance2",
        targets=[game_target],
        library_subpath="KCD2/Mods",
    )
    source = tmp_path / "incoming" / name
    source.mkdir(parents=True, exist_ok=True)
    (source / "file.txt").write_text(name, encoding="utf-8")
    state, _ = import_mod(config, State(), source, game_id="kcd2")
    return config, state


def _add_mod(config: Config, state: State, tmp_path: Path, name: str) -> State:
    source = tmp_path / "incoming" / name
    source.mkdir(parents=True, exist_ok=True)
    (source / "file.txt").write_text(name, encoding="utf-8")
    updated, _ = import_mod(config, state, source, game_id="kcd2")
    return updated


def test_identify_mods_updates_state(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    updated, results, failures = identify_mods(
        config, state, "kcd2", client=FakeClient()
    )
    assert len(results) == 1
    assert failures == []
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
    updated, results, failures = check_for_updates(
        config, seeded, "kcd2", client=FakeClient()
    )
    assert len(results) == 1
    assert failures == []
    assert results[0].latest_version == "1.2.0"
    assert updated.mods[0].update_available is True
    assert updated.mods[0].latest_version == "1.2.0"


def test_identify_mods_continues_after_md5_failure(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path, "moda")
    state = _add_mod(config, state, tmp_path, "modb")

    client = FakeClient(fail_first_md5=True)
    updated, results, failures = identify_mods(config, state, "kcd2", client=client)

    assert len(failures) == 1
    assert failures[0].mod_ref == "kcd2/moda"
    assert "md5_search failed" in failures[0].error
    assert len(results) == 1
    assert results[0].mod_ref == "kcd2/modb"
    assert updated.mods[0].nexus_mod_id is None
    assert updated.mods[1].nexus_mod_id == 43


def test_check_for_updates_continues_after_mod_files_failure(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path, "moda")
    state = _add_mod(config, state, tmp_path, "modb")
    for index, mod_id in enumerate((42, 43)):
        state.mods[index] = state.mods[index].model_copy(
            update={
                "nexus_mod_id": mod_id,
                "installed_version": "1.0.0",
                "last_checked": datetime(2020, 1, 1, tzinfo=UTC),
            }
        )

    updated, results, failures = check_for_updates(
        config,
        state,
        "kcd2",
        client=FakeClient(fail_mod_files_for={42}),
    )

    assert len(failures) == 1
    assert failures[0].mod_ref == "kcd2/moda"
    assert "mod_files failed" in failures[0].error
    assert len(results) == 1
    assert results[0].mod_ref == "kcd2/modb"
    assert updated.mods[0].last_checked == datetime(2020, 1, 1, tzinfo=UTC)
    assert updated.mods[1].update_available is True
    assert updated.mods[1].last_checked is not None


def test_identify_mods_unknown_game_raises_config_error(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    with pytest.raises(ConfigError, match="Unknown game profile"):
        identify_mods(config, state, "bogus", client=FakeClient())


def test_check_for_updates_unknown_game_raises_config_error(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    with pytest.raises(ConfigError, match="Unknown game profile"):
        check_for_updates(config, state, "bogus", client=FakeClient())


def test_is_newer_version_semver_and_fallback() -> None:
    assert is_newer_version("1.0.0", "1.0.1") is True
    assert is_newer_version("1.0.1", "1.0.0") is False
    assert is_newer_version("abc", "def") is True
    assert is_newer_version("abc", "abc") is False
