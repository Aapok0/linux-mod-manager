"""Tests for Nexus identify/check workflows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fixtures.nexus import FakeNexusClient
from lmm.config import Config, ConfigError, add_game_profile
from lmm.library import import_mod
from lmm.nexus.updates import (
    _pick_md5_match,
    _pick_primary_file,
    check_for_updates,
    identify_mods,
    is_newer_version,
    plan_check,
    plan_identify,
    version_compare_used_fallback,
)
from lmm.state import State


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
    state, _, _ = import_mod(config, State(), source, game_id="kcd2")
    return config, state


def _add_mod(config: Config, state: State, tmp_path: Path, name: str) -> State:
    source = tmp_path / "incoming" / name
    source.mkdir(parents=True, exist_ok=True)
    (source / "file.txt").write_text(name, encoding="utf-8")
    updated, _, _ = import_mod(config, state, source, game_id="kcd2")
    return updated


def test_identify_mods_updates_state(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    updated, results, failures, skips = identify_mods(
        config, state, "kcd2", client=FakeNexusClient()
    )
    assert len(results) == 1
    assert failures == []
    assert skips == []
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
    updated, results, failures, version_fallback = check_for_updates(
        config, seeded, "kcd2", client=FakeNexusClient()
    )
    assert len(results) == 1
    assert failures == []
    assert version_fallback is False
    assert results[0].latest_version == "1.2.0"
    assert updated.mods[0].update_available is True
    assert updated.mods[0].latest_version == "1.2.0"


def test_identify_mods_continues_after_md5_failure(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path, "moda")
    state = _add_mod(config, state, tmp_path, "modb")

    client = FakeNexusClient(fail_first_md5=True)
    updated, results, failures, skips = identify_mods(
        config, state, "kcd2", client=client
    )

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

    updated, results, failures, version_fallback = check_for_updates(
        config,
        state,
        "kcd2",
        client=FakeNexusClient(fail_mod_files_for={42}),
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
        identify_mods(config, state, "bogus", client=FakeNexusClient())


def test_check_for_updates_unknown_game_raises_config_error(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    with pytest.raises(ConfigError, match="Unknown game profile"):
        check_for_updates(config, state, "bogus", client=FakeNexusClient())


def test_is_newer_version_semver_and_fallback() -> None:
    assert is_newer_version("1.0.0", "1.0.1") is True
    assert is_newer_version("1.0.1", "1.0.0") is False
    assert is_newer_version("abc", "def") is True
    assert is_newer_version("abc", "abc") is False
    assert version_compare_used_fallback("abc", "1.0.0") is True
    assert version_compare_used_fallback("1.0.0", "1.0.1") is False


def test_identify_mods_reports_skip_when_no_nexus_match(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)

    class EmptyClient(FakeNexusClient):
        def md5_search(self, _: str, __: str) -> list[dict]:
            return []

    updated, results, failures, skips = identify_mods(
        config, state, "kcd2", client=EmptyClient()
    )
    assert results == []
    assert failures == []
    assert len(skips) == 1
    assert skips[0].reason == "no_nexus_match"
    assert updated.mods[0].nexus_mod_id is None


def test_pick_primary_file_prefers_archive_over_larger_text(tmp_path: Path) -> None:
    mod_dir = tmp_path / "mod"
    mod_dir.mkdir()
    (mod_dir / "readme.txt").write_text("x" * 1000, encoding="utf-8")
    (mod_dir / "content.pak").write_bytes(b"small")
    picked = _pick_primary_file(mod_dir)
    assert picked is not None
    assert picked.name == "content.pak"


def test_pick_md5_match_prefers_exact_file_size() -> None:
    matches = [
        {"mod_id": 1, "file_details": {"size": 999}},
        {"mod_id": 2, "file_details": {"size": 100}},
    ]
    chosen = _pick_md5_match(matches, 100)
    assert chosen["mod_id"] == 2


def test_identify_mods_disambiguates_md5_matches_by_size(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    content = b"abc"
    source = state.mods[0].source_path
    (source / "mod.pak").write_bytes(content)

    class SizedClient(FakeNexusClient):
        def md5_search(self, _: str, __: str) -> list[dict]:
            return [
                {"mod_id": 1, "file_details": {"size": 999}},
                {"mod_id": 42, "file_details": {"size": len(content)}},
            ]

    updated, results, failures, skips = identify_mods(
        config, state, "kcd2", client=SizedClient()
    )
    assert failures == []
    assert skips == []
    assert len(results) == 1
    assert results[0].nexus_mod_id == 42
    assert updated.mods[0].nexus_mod_id == 42


def test_plan_identify_lists_candidates(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    planned = plan_identify(config, state, "kcd2")
    assert len(planned) == 1
    assert planned[0].mod_ref == "kcd2/moda"
    assert planned[0].source_file is not None


def test_check_for_updates_skips_fresh_mod_not_in_updated_set(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    seeded = state.model_copy(deep=True)
    seeded.mods[0] = seeded.mods[0].model_copy(
        update={
            "nexus_mod_id": 99,
            "installed_version": "1.0.0",
            "last_checked": datetime.now(UTC),
        }
    )
    client = FakeNexusClient()
    client.updated_payload = []
    _, _, _, version_fallback = check_for_updates(config, seeded, "kcd2", client=client)
    assert version_fallback is False
    assert client.mod_files_calls == []


def test_plan_check_includes_stale_mod(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    stale_time = datetime.now(UTC) - timedelta(hours=25)
    state.mods[0] = state.mods[0].model_copy(
        update={"nexus_mod_id": 42, "last_checked": stale_time}
    )
    planned = plan_check(config, state, "kcd2")
    assert len(planned) == 1
    assert planned[0].reason == "stale"


def test_plan_check_skips_fresh_mod(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    state.mods[0] = state.mods[0].model_copy(
        update={"nexus_mod_id": 42, "last_checked": datetime.now(UTC)}
    )
    planned = plan_check(config, state, "kcd2")
    assert planned == []


def test_plan_check_skips_mod_without_nexus_id(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    planned = plan_check(config, state, "kcd2")
    assert planned == []


def test_check_for_updates_marks_up_to_date_mod(tmp_path: Path) -> None:
    config, state = _setup_mod(tmp_path)
    seeded = state.model_copy(deep=True)
    seeded.mods[0] = seeded.mods[0].model_copy(
        update={
            "nexus_mod_id": 42,
            "installed_version": "1.2.0",
            "last_checked": datetime(2020, 1, 1, tzinfo=UTC),
        }
    )

    class UpToDateClient(FakeNexusClient):
        def mod_files(self, _: str, mod_id: int) -> list[dict]:
            return [
                {
                    "file_id": 7,
                    "version": "1.2.0",
                    "category_name": "MAIN",
                    "uploaded_timestamp": 1000,
                }
            ]

    updated, results, failures, _ = check_for_updates(
        config, seeded, "kcd2", client=UpToDateClient()
    )
    assert failures == []
    assert results == []
    assert updated.mods[0].update_available is False


@pytest.mark.parametrize(
    ("installed", "latest", "expected"),
    [
        ("1.0", "1.0.0.0", True),
        ("1.0.0", "1.0.1", True),
        ("", "1.0.0", True),
        ("1.0.0", "", False),
    ],
)
def test_is_newer_version_edge_cases(
    installed: str, latest: str, expected: bool
) -> None:
    assert is_newer_version(installed or None, latest or None) is expected
