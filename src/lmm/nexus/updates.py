"""Nexus identify/check workflows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lmm.config import Config
from lmm.nexus.client import NexusClient
from lmm.state import ModRecord, State


@dataclass
class IdentifyResult:
    mod_ref: str
    nexus_mod_id: int
    file_id: int | None
    installed_version: str | None


@dataclass
class UpdateResult:
    mod_ref: str
    installed_version: str | None
    latest_version: str


def _mod_ref(mod: ModRecord) -> str:
    return f"{mod.game}/{mod.name}"


def _extract_int(mapping: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int):
            return value
    return None


def _extract_str(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _pick_primary_file(mod_source: Path) -> Path | None:
    files = [path for path in mod_source.rglob("*") if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda item: item.stat().st_size)


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - required by Nexus md5_search
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _entry_mod_id(entry: dict[str, Any]) -> int | None:
    mod_section = entry.get("mod")
    if isinstance(mod_section, dict):
        nested_id = _extract_int(mod_section, "mod_id", "id")
        if nested_id is not None:
            return nested_id
    return _extract_int(entry, "mod_id", "modId", "id")


def _entry_file_id(entry: dict[str, Any]) -> int | None:
    file_section = entry.get("file_details")
    if isinstance(file_section, dict):
        nested_id = _extract_int(file_section, "file_id", "id")
        if nested_id is not None:
            return nested_id
    return _extract_int(entry, "file_id", "fileId")


def _entry_version(entry: dict[str, Any]) -> str | None:
    file_section = entry.get("file_details")
    if isinstance(file_section, dict):
        version = _extract_str(file_section, "version")
        if version is not None:
            return version
    return _extract_str(entry, "version")


def identify_mods(
    config: Config,
    state: State,
    game_id: str,
    *,
    client: NexusClient,
) -> tuple[State, list[IdentifyResult]]:
    profile = config.games.get(game_id)
    if profile is None:
        msg = f"Unknown game profile: {game_id}"
        raise ValueError(msg)

    updated = state.model_copy(deep=True)
    results: list[IdentifyResult] = []
    for index, mod in enumerate(updated.mods):
        if mod.game != game_id or mod.nexus_mod_id is not None:
            continue
        candidate = _pick_primary_file(mod.source_path.resolve())
        if candidate is None:
            continue
        md5_hash = mod.file_md5 or _file_md5(candidate)
        matches = client.md5_search(profile.nexus_domain, md5_hash)
        if not matches:
            continue
        chosen = matches[0]
        nexus_mod_id = _entry_mod_id(chosen)
        if nexus_mod_id is None:
            continue
        file_id = _entry_file_id(chosen)
        version = _entry_version(chosen)
        updated_mod = mod.model_copy(
            update={
                "file_md5": md5_hash,
                "nexus_mod_id": nexus_mod_id,
                "file_id": file_id,
                "installed_version": version or mod.installed_version,
            }
        )
        updated.mods[index] = updated_mod
        results.append(
            IdentifyResult(
                mod_ref=_mod_ref(updated_mod),
                nexus_mod_id=nexus_mod_id,
                file_id=file_id,
                installed_version=updated_mod.installed_version,
            )
        )
    return updated, results


def _normalize_version(version: str) -> tuple[int, ...] | None:
    parts = version.strip().lstrip("vV").split(".")
    normalized: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        normalized.append(int(part))
    return tuple(normalized)


def is_newer_version(installed: str | None, latest: str | None) -> bool:
    if latest is None:
        return False
    if installed is None:
        return True
    left = _normalize_version(installed)
    right = _normalize_version(latest)
    if left is not None and right is not None:
        return right > left
    return latest != installed


def _file_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    category = _extract_str(item, "category_name", "category")
    category_score = 1 if category and category.upper() == "MAIN" else 0
    primary_score = 1 if bool(item.get("is_primary")) else 0
    upload_score = 0
    upload_time = item.get("uploaded_timestamp")
    if isinstance(upload_time, int):
        upload_score = upload_time
    return (category_score, primary_score, upload_score)


def _latest_file_version(files: list[dict[str, Any]]) -> tuple[int | None, str | None]:
    if not files:
        return None, None
    best = sorted(files, key=_file_sort_key, reverse=True)[0]
    return _extract_int(best, "file_id", "id"), _extract_str(best, "version")


def _updated_mod_ids(payload: list[dict[str, Any]]) -> set[int]:
    ids: set[int] = set()
    for item in payload:
        mod_id = _extract_int(item, "mod_id", "modId")
        if mod_id is not None:
            ids.add(mod_id)
    return ids


def check_for_updates(
    config: Config,
    state: State,
    game_id: str,
    *,
    client: NexusClient,
    period: str = "1w",
    stale_after: timedelta = timedelta(hours=24),
) -> tuple[State, list[UpdateResult]]:
    profile = config.games.get(game_id)
    if profile is None:
        msg = f"Unknown game profile: {game_id}"
        raise ValueError(msg)

    now = datetime.now(UTC)
    recent = client.updated_mods(profile.nexus_domain, period=period)
    updated_ids = _updated_mod_ids(recent)
    updated = state.model_copy(deep=True)
    changes: list[UpdateResult] = []

    for index, mod in enumerate(updated.mods):
        if mod.game != game_id or mod.nexus_mod_id is None:
            continue
        stale = mod.last_checked is None or (now - mod.last_checked) >= stale_after
        if mod.nexus_mod_id not in updated_ids and not stale:
            continue
        files = client.mod_files(profile.nexus_domain, mod.nexus_mod_id)
        file_id, latest = _latest_file_version(files)
        has_update = is_newer_version(mod.installed_version, latest)
        updated_mod = mod.model_copy(
            update={
                "file_id": file_id if file_id is not None else mod.file_id,
                "latest_version": latest,
                "update_available": has_update,
                "last_checked": now,
            }
        )
        updated.mods[index] = updated_mod
        if has_update and latest is not None:
            changes.append(
                UpdateResult(
                    mod_ref=_mod_ref(updated_mod),
                    installed_version=updated_mod.installed_version,
                    latest_version=latest,
                )
            )
    return updated, changes
