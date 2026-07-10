"""CLI tests for Nexus commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lmm.cli import app
from lmm.nexus import NexusError
from lmm.state import StateStore


class FakeNexusClient:
    def __init__(self, *, api_key: str | None, **_: Any) -> None:
        if not api_key:
            msg = "Nexus API key missing. Set NEXUS_API_KEY or config.nexus_api_key."
            raise NexusError(msg)
        self._md5_calls = 0

    def __enter__(self) -> FakeNexusClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def validate_key(self) -> dict[str, str]:
        return {"name": "tester"}

    def md5_search(self, _: str, __: str) -> list[dict[str, Any]]:
        self._md5_calls += 1
        if self._md5_calls == 1:
            msg = "md5_search failed for first mod"
            raise NexusError(msg)
        return [{"mod_id": 99, "file_id": 5, "version": "1.0.0"}]

    def updated_mods(self, _: str, *, period: str = "1w") -> list[dict[str, int]]:
        return [{"mod_id": 99}]

    def mod_files(self, _: str, __: int) -> list[dict[str, Any]]:
        return [{"file_id": 5, "version": "1.1.0", "category_name": "MAIN"}]


class HappyNexusClient(FakeNexusClient):
    def md5_search(self, _: str, __: str) -> list[dict[str, Any]]:
        return [{"mod_id": 99, "file_id": 5, "version": "1.0.0"}]


def test_identify_and_check_commands(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXUS_API_KEY", "secret")
    monkeypatch.setattr("lmm.cli.NexusClient", HappyNexusClient)

    source = data_dir / "incoming" / "moda"
    source.mkdir(parents=True)
    (source / "file.txt").write_text("abc", encoding="utf-8")
    runner.invoke(app, [*cli_args, "add", str(source), "--game", "kcd2"])

    identify = runner.invoke(app, [*cli_args, "identify", "kcd2"])
    assert identify.exit_code == 0, identify.output

    state = StateStore(data_dir / "state.json").load()
    assert state.mods[0].nexus_mod_id == 99

    check = runner.invoke(app, [*cli_args, "check", "kcd2"])
    assert check.exit_code == 0, check.output
    state = StateStore(data_dir / "state.json").load()
    assert state.mods[0].latest_version == "1.1.0"


def test_identify_requires_key(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEXUS_API_KEY", raising=False)

    source = data_dir / "incoming" / "moda"
    source.mkdir(parents=True)
    (source / "file.txt").write_text("abc", encoding="utf-8")
    runner.invoke(app, [*cli_args, "add", str(source), "--game", "kcd2"])

    identify = runner.invoke(app, [*cli_args, "identify", "kcd2"])
    assert identify.exit_code == 1
    assert "Nexus API key missing" in identify.output


def test_identify_unknown_game_reports_error(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXUS_API_KEY", "secret")
    monkeypatch.setattr("lmm.cli.NexusClient", HappyNexusClient)

    identify = runner.invoke(app, [*cli_args, "identify", "bogus"])
    assert identify.exit_code == 1
    assert "Unknown game profile: bogus" in identify.output


def test_identify_continues_after_per_mod_failure(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXUS_API_KEY", "secret")
    monkeypatch.setattr("lmm.cli.NexusClient", FakeNexusClient)

    for name in ("moda", "modb"):
        source = data_dir / "incoming" / name
        source.mkdir(parents=True)
        (source / "file.txt").write_text(name, encoding="utf-8")
        runner.invoke(app, [*cli_args, "add", str(source), "--game", "kcd2"])

    identify = runner.invoke(app, [*cli_args, "identify", "kcd2"])
    assert identify.exit_code == 1, identify.output
    assert "md5_search failed" in identify.output

    state = StateStore(data_dir / "state.json").load()
    assert state.mods[0].nexus_mod_id is None
    assert state.mods[1].nexus_mod_id == 99


def test_identify_dry_run_skips_nexus_and_state(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXUS_API_KEY", "secret")

    def fail_if_called(*_: object, **__: object) -> None:
        msg = "NexusClient should not be used in dry-run"
        raise AssertionError(msg)

    monkeypatch.setattr("lmm.cli.NexusClient", fail_if_called)

    source = data_dir / "incoming" / "moda"
    source.mkdir(parents=True)
    (source / "file.txt").write_text("abc", encoding="utf-8")
    runner.invoke(app, [*cli_args, "add", str(source), "--game", "kcd2"])

    before = StateStore(data_dir / "state.json").load()
    identify = runner.invoke(app, [*cli_args, "--dry-run", "identify", "kcd2"])
    assert identify.exit_code == 0, identify.output
    assert "would query Nexus" in identify.output

    after = StateStore(data_dir / "state.json").load()
    assert after.mods[0].nexus_mod_id == before.mods[0].nexus_mod_id


def test_identify_json_reports_failures_with_exit_one(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXUS_API_KEY", "secret")
    monkeypatch.setattr("lmm.cli.NexusClient", FakeNexusClient)

    source = data_dir / "incoming" / "moda"
    source.mkdir(parents=True)
    (source / "file.txt").write_text("abc", encoding="utf-8")
    runner.invoke(app, [*cli_args, "add", str(source), "--game", "kcd2"])

    identify = runner.invoke(app, [*cli_args, "--json", "identify", "kcd2"])
    assert identify.exit_code == 1, identify.output
    assert '"failures"' in identify.output


def test_check_dry_run_smoke(
    runner: CliRunner,
    cli_args: list[str],
    kcd2_profile: None,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXUS_API_KEY", "secret")

    def fail_if_called(*_: object, **__: object) -> None:
        msg = "NexusClient should not be used in dry-run"
        raise AssertionError(msg)

    monkeypatch.setattr("lmm.cli.NexusClient", fail_if_called)

    source = data_dir / "incoming" / "moda"
    source.mkdir(parents=True)
    (source / "file.txt").write_text("abc", encoding="utf-8")
    runner.invoke(app, [*cli_args, "add", str(source), "--game", "kcd2"])

    check = runner.invoke(app, [*cli_args, "--dry-run", "check", "kcd2"])
    assert check.exit_code == 0, check.output
    assert "would query Nexus" in check.output
