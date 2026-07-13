"""Nexus identify/check workflows."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lmm.config import Config, ConfigError
from lmm.nexus.client import NexusClient, NexusError
from lmm.state import ModRecord, State

ARCHIVE_SUFFIXES = frozenset({".zip", ".7z", ".rar", ".pak", ".ba2", ".mpmod"})


@dataclass
class IdentifyPlanItem:
    mod_ref: str
    source_file: Path | None


@dataclass
class CheckPlanItem:
    mod_ref: str
    reason: str


@dataclass
class IdentifyResult:
    mod_ref: str
    nexus_mod_id: int
    file_id: int | None
    installed_version: str | None


@dataclass
class IdentifyFailure:
    mod_ref: str
    error: str


@dataclass
class IdentifySkip:
    mod_ref: str
    reason: str


@dataclass
class UpdateResult:
    mod_ref: str
    installed_version: str | None
    latest_version: str
    non_numeric_versions: bool = False


@dataclass
class UpdateFailure:
    mod_ref: str
    error: str


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
    archives = [path for path in files if path.suffix.lower() in ARCHIVE_SUFFIXES]
    candidates = archives if archives else files
    return max(candidates, key=lambda item: item.stat().st_size)


def _entry_file_size(entry: dict[str, Any]) -> int | None:
    file_section = entry.get("file_details")
    if isinstance(file_section, dict):
        size = _extract_int(file_section, "size", "file_size")
        if size is not None:
            return size
    return _extract_int(entry, "size", "file_size")


def _is_main_category(entry: dict[str, Any]) -> bool:
    category = _extract_str(entry, "category_name", "category")
    if category and category.upper() == "MAIN":
        return True
    file_section = entry.get("file_details")
    if isinstance(file_section, dict):
        nested = _extract_str(file_section, "category_name", "category")
        return bool(nested and nested.upper() == "MAIN")
    return False


def _pick_md5_match(matches: list[dict[str, Any]], local_size: int) -> dict[str, Any]:
    if not matches:
        msg = "matches must not be empty"
        raise ValueError(msg)
    sized = [(entry, _entry_file_size(entry)) for entry in matches]
    if all(size is None for _, size in sized):
        return matches[0]
    exact = [entry for entry, size in sized if size == local_size]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        main_matches = [entry for entry in exact if _is_main_category(entry)]
        if len(main_matches) == 1:
            return main_matches[0]
        return exact[0]
    with_size = [(entry, size) for entry, size in sized if size is not None]
    return min(with_size, key=lambda item: abs(item[1] - local_size))[0]


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
    on_progress: Callable[[str, str], None] | None = None,
) -> tuple[State, list[IdentifyResult], list[IdentifyFailure], list[IdentifySkip]]:
    profile = config.games.get(game_id)
    if profile is None:
        msg = f"Unknown game profile: {game_id}"
        raise ConfigError(msg)

    updated = state.model_copy(deep=True)
    results: list[IdentifyResult] = []
    failures: list[IdentifyFailure] = []
    skips: list[IdentifySkip] = []
    for index, mod in enumerate(updated.mods):
        if mod.game != game_id or mod.nexus_mod_id is not None:
            continue
        mod_ref = _mod_ref(mod)
        candidate = _pick_primary_file(mod.source_path.resolve())
        if candidate is None:
            skips.append(IdentifySkip(mod_ref=mod_ref, reason="no_hashable_file"))
            continue
        if on_progress:
            on_progress(mod_ref, "hashing")
        md5_hash = mod.file_md5 or _file_md5(candidate)
        if on_progress:
            on_progress(mod_ref, "querying")
        try:
            matches = client.md5_search(profile.nexus_domain, md5_hash)
        except NexusError as exc:
            failures.append(IdentifyFailure(mod_ref=mod_ref, error=str(exc)))
            continue
        if not matches:
            skips.append(IdentifySkip(mod_ref=mod_ref, reason="no_nexus_match"))
            continue
        chosen = _pick_md5_match(matches, candidate.stat().st_size)
        nexus_mod_id = _entry_mod_id(chosen)
        if nexus_mod_id is None:
            skips.append(IdentifySkip(mod_ref=mod_ref, reason="no_nexus_match"))
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
    return updated, results, failures, skips


def plan_identify(
    config: Config,
    state: State,
    game_id: str,
) -> list[IdentifyPlanItem]:
    profile = config.games.get(game_id)
    if profile is None:
        msg = f"Unknown game profile: {game_id}"
        raise ConfigError(msg)
    planned: list[IdentifyPlanItem] = []
    for mod in state.mods:
        if mod.game != game_id or mod.nexus_mod_id is not None:
            continue
        candidate = _pick_primary_file(mod.source_path.resolve())
        planned.append(IdentifyPlanItem(mod_ref=_mod_ref(mod), source_file=candidate))
    return planned


def plan_check(
    config: Config,
    state: State,
    game_id: str,
    *,
    stale_after: timedelta = timedelta(hours=24),
) -> list[CheckPlanItem]:
    profile = config.games.get(game_id)
    if profile is None:
        msg = f"Unknown game profile: {game_id}"
        raise ConfigError(msg)
    now = datetime.now(UTC)
    planned: list[CheckPlanItem] = []
    for mod in state.mods:
        if mod.game != game_id or mod.nexus_mod_id is None:
            continue
        stale = mod.last_checked is None or (now - mod.last_checked) >= stale_after
        if stale:
            planned.append(CheckPlanItem(mod_ref=_mod_ref(mod), reason="stale"))
    return planned


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


def version_compare_used_fallback(installed: str | None, latest: str | None) -> bool:
    if latest is None or installed is None:
        return False
    left = _normalize_version(installed)
    right = _normalize_version(latest)
    return left is None or right is None


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
    on_progress: Callable[[str, str], None] | None = None,
) -> tuple[State, list[UpdateResult], list[UpdateFailure], bool]:
    profile = config.games.get(game_id)
    if profile is None:
        msg = f"Unknown game profile: {game_id}"
        raise ConfigError(msg)

    now = datetime.now(UTC)
    recent = client.updated_mods(profile.nexus_domain, period=period)
    updated_ids = _updated_mod_ids(recent)
    updated = state.model_copy(deep=True)
    changes: list[UpdateResult] = []
    failures: list[UpdateFailure] = []
    version_fallback_used = False

    for index, mod in enumerate(updated.mods):
        if mod.game != game_id or mod.nexus_mod_id is None:
            continue
        stale = mod.last_checked is None or (now - mod.last_checked) >= stale_after
        if mod.nexus_mod_id not in updated_ids and not stale:
            continue
        mod_ref = _mod_ref(mod)
        if on_progress:
            on_progress(mod_ref, "querying")
        try:
            files = client.mod_files(profile.nexus_domain, mod.nexus_mod_id)
        except NexusError as exc:
            failures.append(UpdateFailure(mod_ref=mod_ref, error=str(exc)))
            continue
        file_id, latest = _latest_file_version(files)
        fallback = version_compare_used_fallback(mod.installed_version, latest)
        if fallback:
            version_fallback_used = True
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
                    non_numeric_versions=fallback,
                )
            )
    return updated, changes, failures, version_fallback_used
