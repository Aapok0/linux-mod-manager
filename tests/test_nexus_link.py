"""Tests for manual Nexus mod linking."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from fixtures.nexus import FakeNexusClient
from lmm.config import Config, add_game_profile
from lmm.library import import_mod
from lmm.nexus.link import LinkError, link_mod_record, parse_nexus_mod_url
from lmm.state import State


def _make_mod_zip(path: Path, mod_name: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{mod_name}/mod.manifest", "x")


def test_parse_nexus_mod_url() -> None:
    domain, mod_id = parse_nexus_mod_url(
        "https://www.nexusmods.com/kingdomcomedeliverance2/mods/68"
    )
    assert domain == "kingdomcomedeliverance2"
    assert mod_id == 68


def test_parse_nexus_mod_url_invalid() -> None:
    with pytest.raises(LinkError, match="Not a Nexus mod URL"):
        parse_nexus_mod_url("https://example.com/mods/68")


def test_link_mod_record_by_mod_id(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    config = add_game_profile(
        Config(library_root=library_root),
        "kcd2",
        nexus_domain="kingdomcomedeliverance2",
        targets=[tmp_path / "game" / "Mods"],
    )
    archive = tmp_path / "mod.zip"
    _make_mod_zip(archive, "mod")
    _, mod, _ = import_mod(config, State(), archive, game_id="kcd2")
    linked = link_mod_record(
        config,
        mod,
        client=FakeNexusClient(),
        mod_id=68,
    )
    assert linked.nexus_mod_id == 68
    assert linked.file_id == 7
    assert linked.installed_version == "2.0.0"
