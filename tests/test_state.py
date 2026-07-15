"""Tests for state load/save and mod records."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lmm.state import (
    ModRecord,
    State,
    StateError,
    StateStore,
    add_mod_record,
    find_mod,
    remove_mod_record,
    set_mod_enabled,
)


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    state = State(
        mods=[
            ModRecord(
                name="easysharpening",
                game="kcd2",
                source_path=Path("/tmp/library/easysharpening"),
                nexus_mod_id=42,
                installed_version="1.0.0",
            )
        ]
    )
    store.save(state)
    loaded = store.load()
    assert loaded.schema_version == 1
    assert len(loaded.mods) == 1
    mod = loaded.mods[0]
    assert mod.name == "easysharpening"
    assert mod.game == "kcd2"
    assert mod.source_path == Path("/tmp/library/easysharpening")
    assert mod.nexus_mod_id == 42
    assert mod.installed_version == "1.0.0"


def test_find_mod_by_game_and_name() -> None:
    state = State(
        mods=[
            ModRecord(
                name="foo",
                game="kcd2",
                source_path=Path("/tmp/foo"),
            )
        ]
    )
    mod = find_mod(state, "kcd2/foo")
    assert mod.name == "foo"


def test_find_mod_ambiguous() -> None:
    state = State(
        mods=[
            ModRecord(name="foo", game="a", source_path=Path("/a")),
            ModRecord(name="foo", game="b", source_path=Path("/b")),
        ]
    )
    with pytest.raises(StateError, match="matches:"):
        find_mod(state, "foo")


def test_remove_mod_record() -> None:
    state = State(
        mods=[ModRecord(name="foo", game="kcd2", source_path=Path("/tmp/foo"))]
    )
    updated = remove_mod_record(state, "kcd2", "foo")
    assert updated.mods == []
    with pytest.raises(StateError, match="Mod not found"):
        remove_mod_record(updated, "kcd2", "foo")


def test_add_mod_record_rejects_duplicate() -> None:
    state = State(
        mods=[ModRecord(name="foo", game="kcd2", source_path=Path("/tmp/foo"))]
    )
    with pytest.raises(ValueError, match="already exists"):
        add_mod_record(
            state,
            ModRecord(name="foo", game="kcd2", source_path=Path("/tmp/bar")),
        )


def test_find_mod_with_default_game() -> None:
    state = State(
        mods=[
            ModRecord(name="foo", game="a", source_path=Path("/a")),
            ModRecord(name="foo", game="b", source_path=Path("/b")),
        ]
    )
    mod = find_mod(state, "foo", default_game="b")
    assert mod.game == "b"


def test_corrupt_state_raises_state_error(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError, match="Invalid JSON"):
        StateStore(path).load()


def test_unsupported_schema_raises_state_error(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"schema_version": 99, "mods": []}', encoding="utf-8")
    with pytest.raises(StateError, match="Unsupported state schema"):
        StateStore(path).load()


def test_saved_json_has_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    StateStore(path).save(State())
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1


def test_set_mod_enabled_toggles() -> None:
    state = State(
        mods=[ModRecord(name="foo", game="kcd2", source_path=Path("/tmp/foo"))]
    )
    disabled, record = set_mod_enabled(state, "foo", enabled=False, default_game="kcd2")
    assert record.enabled is False
    enabled, record = set_mod_enabled(
        disabled, "foo", enabled=True, default_game="kcd2"
    )
    assert record.enabled is True
    again, record = set_mod_enabled(enabled, "foo", enabled=True, default_game="kcd2")
    assert again.mods[0].enabled is True


def test_malformed_mod_record_raises_state_error(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"schema_version": 1, "mods": [{"name": "x"}]}),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="Invalid state"):
        StateStore(path).load()


def test_unreadable_state_raises_state_error(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.mkdir()
    with pytest.raises(StateError, match="Cannot read state"):
        StateStore(path).load()
