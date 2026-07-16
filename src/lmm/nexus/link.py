"""Manual Nexus mod linking and URL parsing."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lmm.config import Config, ConfigError
from lmm.nexus.client import NexusClient, NexusError
from lmm.nexus.updates_hash import (
    _entry_file_id,
    _extract_int,
    _extract_str,
    _file_md5,
    _file_sort_key,
    _is_main_category,
)
from lmm.state import ModRecord

_NEXUS_MOD_URL = re.compile(
    r"nexusmods\.com/(?P<domain>[^/]+)/mods/(?P<mod_id>\d+)",
    re.IGNORECASE,
)


class LinkError(Exception):
    """Raised when mod linking fails."""


def parse_nexus_mod_url(url: str) -> tuple[str, int]:
    match = _NEXUS_MOD_URL.search(url.strip())
    if match is None:
        msg = f"Not a Nexus mod URL: {url}"
        raise LinkError(msg)
    mod_id = int(match.group("mod_id"))
    return match.group("domain"), mod_id


def _file_entry_md5(entry: dict[str, Any]) -> str | None:
    for key in ("md5", "content_md5", "file_md5"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    file_section = entry.get("file_details")
    if isinstance(file_section, dict):
        for key in ("md5", "content_md5", "file_md5"):
            value = file_section.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return None


def _file_entry_size(entry: dict[str, Any]) -> int | None:
    size = _extract_int(entry, "size", "file_size", "size_in_bytes", "size_kb")
    if size is not None and "size_kb" in entry and entry.get("size_kb") == size:
        return size * 1024
    file_section = entry.get("file_details")
    if isinstance(file_section, dict):
        nested = _extract_int(
            file_section, "size", "file_size", "size_in_bytes", "size_kb"
        )
        if nested is not None:
            return nested
    return size


def _match_download_file(
    files: list[dict[str, Any]], download_path: Path
) -> dict[str, Any] | None:
    if not files or not download_path.is_file():
        return None
    local_md5 = _file_md5(download_path)
    local_size = download_path.stat().st_size
    md5_matches = [entry for entry in files if _file_entry_md5(entry) == local_md5]
    if len(md5_matches) == 1:
        return md5_matches[0]
    if md5_matches:
        sized = [
            entry for entry in md5_matches if _file_entry_size(entry) == local_size
        ]
        if len(sized) == 1:
            return sized[0]
        main = [entry for entry in md5_matches if _is_main_category(entry)]
        if len(main) == 1:
            return main[0]
        return md5_matches[0]
    size_matches = [entry for entry in files if _file_entry_size(entry) == local_size]
    if len(size_matches) == 1:
        return size_matches[0]
    return None


def _latest_main_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not files:
        return None
    return sorted(files, key=_file_sort_key, reverse=True)[0]


def link_mod_record(
    config: Config,
    mod: ModRecord,
    *,
    client: NexusClient,
    mod_id: int | None = None,
    url: str | None = None,
) -> ModRecord:
    profile = config.games.get(mod.game)
    if profile is None:
        msg = f"Unknown game profile: {mod.game}"
        raise ConfigError(msg)
    if mod_id is None and url is None:
        msg = "Provide mod_id or url"
        raise LinkError(msg)
    if mod_id is None:
        domain, parsed_id = parse_nexus_mod_url(url or "")
        if domain.lower() != profile.nexus_domain.lower():
            msg = (
                f"URL domain {domain!r} does not match game profile "
                f"{profile.nexus_domain!r}"
            )
            raise LinkError(msg)
        mod_id = parsed_id
    try:
        files = client.mod_files(profile.nexus_domain, mod_id)
    except NexusError as exc:
        msg = f"Cannot fetch files for mod {mod_id}: {exc}"
        raise LinkError(msg) from exc
    matched: dict[str, Any] | None = None
    file_md5: str | None = mod.file_md5
    if mod.download_path is not None and mod.download_path.is_file():
        matched = _match_download_file(files, mod.download_path)
        file_md5 = file_md5 or _file_md5(mod.download_path)
    fallback = matched or _latest_main_file(files)
    file_id = _entry_file_id(fallback) if fallback else None
    version = _extract_str(fallback or {}, "version") if fallback else None
    return mod.model_copy(
        update={
            "nexus_mod_id": mod_id,
            "file_id": file_id,
            "installed_version": version or mod.installed_version,
            "file_md5": file_md5,
        }
    )


def normalize_name(value: str) -> str:
    cleaned = value.lower()
    for char in ("_", "-", " ", ".", "(", ")", "[", "]"):
        cleaned = cleaned.replace(char, "")
    return cleaned


def match_tracked_mod(
    mod: ModRecord,
    tracked: list[dict[str, Any]],
    game_domain: str,
) -> int | None:
    mod_name = normalize_name(mod.name)
    matches: list[int] = []
    for entry in tracked:
        entry_domain = _extract_str(entry, "game_domain_name", "domain_name", "game")
        if entry_domain and entry_domain.lower() != game_domain.lower():
            continue
        entry_name = _extract_str(entry, "name", "mod_name")
        if entry_name is None:
            continue
        if normalize_name(entry_name) == mod_name:
            mod_id = _extract_int(entry, "mod_id", "modId", "id")
            if mod_id is not None:
                matches.append(mod_id)
    if len(matches) == 1:
        return matches[0]
    return None
