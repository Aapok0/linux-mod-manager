"""Shared hash helpers for Nexus identify workflows."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - required by Nexus md5_search
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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


def _entry_file_size(entry: dict[str, Any]) -> int | None:
    file_section = entry.get("file_details")
    if isinstance(file_section, dict):
        size = _extract_int(file_section, "size", "file_size")
        if size is not None:
            return size
    return _extract_int(entry, "size", "file_size")


def _entry_file_id(entry: dict[str, Any]) -> int | None:
    file_section = entry.get("file_details")
    if isinstance(file_section, dict):
        nested_id = _extract_int(file_section, "file_id", "id")
        if nested_id is not None:
            return nested_id
    return _extract_int(entry, "file_id", "fileId", "id")


def _is_main_category(entry: dict[str, Any]) -> bool:
    category = _extract_str(entry, "category_name", "category")
    if category and category.upper() == "MAIN":
        return True
    file_section = entry.get("file_details")
    if isinstance(file_section, dict):
        nested = _extract_str(file_section, "category_name", "category")
        return bool(nested and nested.upper() == "MAIN")
    return False


def _file_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    category = _extract_str(item, "category_name", "category")
    category_score = 1 if category and category.upper() == "MAIN" else 0
    primary_score = 1 if bool(item.get("is_primary")) else 0
    upload_score = 0
    upload_time = item.get("uploaded_timestamp")
    if isinstance(upload_time, int):
        upload_score = upload_time
    return (category_score, primary_score, upload_score)


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
