"""Import mods into the library and list registered mods."""

from __future__ import annotations

import enum
import shutil
from dataclasses import dataclass
from pathlib import Path

from lmm.archive import (
    DOWNLOAD_DIRNAME,
    ArchiveError,
    extract_archive,
    is_archive,
    is_download_file,
    is_loose_download,
    peek_archive_root_name,
)
from lmm.config import Config
from lmm.paths import (
    PathValidationError,
    path_within_root,
    resolve_under_root,
    validate_path_segment,
)
from lmm.state import ModRecord, State, add_mod_record


class LibraryError(Exception):
    """Raised when a library operation fails."""


class ImportAction(enum.StrEnum):
    REGISTERED = "registered"
    COPIED = "copied"
    MOVED = "moved"
    EXTRACTED = "extracted"


@dataclass
class ImportSkip:
    path: Path
    reason: str


@dataclass
class ImportFailure:
    name: str
    error: str


@dataclass
class ImportResult:
    record: ModRecord
    action: ImportAction


def _mod_registered(state: State, game_id: str, name: str) -> bool:
    return any(mod.game == game_id and mod.name == name for mod in state.mods)


def discover_download_files(parent: Path) -> list[Path]:
    """Return sorted importable download files at the top level of parent."""
    parent = parent.resolve()
    return sorted(
        entry
        for entry in parent.iterdir()
        if entry.is_file()
        and not entry.name.startswith(".")
        and is_download_file(entry)
    )


def _resolve_download_path(package_root: Path) -> Path | None:
    download_dir = package_root / DOWNLOAD_DIRNAME
    if not download_dir.is_dir():
        return None
    files = sorted(
        entry
        for entry in download_dir.iterdir()
        if entry.is_file() and not entry.name.startswith(".")
    )
    if len(files) == 1:
        return files[0]
    return None


def _is_valid_package_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    download_path = _resolve_download_path(path)
    if download_path is None:
        return False
    for entry in path.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.name == DOWNLOAD_DIRNAME:
            continue
        return True
    return is_loose_download(download_path)


def _derive_mod_name(source: Path, name: str | None) -> str:
    if name is not None:
        return validate_path_segment(name, field="mod name")
    if is_archive(source):
        root_name = peek_archive_root_name(source)
        if root_name:
            return validate_path_segment(root_name, field="mod name")
    return validate_path_segment(source.stem, field="mod name")


def _store_download_file(
    source: Path,
    package_root: Path,
    *,
    copy: bool,
) -> tuple[Path, ImportAction]:
    download_dir = package_root / DOWNLOAD_DIRNAME
    download_dir.mkdir(parents=True, exist_ok=True)
    destination = download_dir / source.name
    if destination.exists():
        msg = f"Download file already exists in package: {destination}"
        raise LibraryError(msg)
    if copy:
        shutil.copy2(source, destination)
        return destination, ImportAction.COPIED
    shutil.move(str(source), str(destination))
    return destination, ImportAction.MOVED


def _import_file_package(
    config: Config,
    source: Path,
    game_id: str,
    *,
    name: str | None,
    copy: bool,
) -> tuple[Path, Path, ImportAction]:
    mod_name = _derive_mod_name(source, name)
    package_root = resolve_mod_destination(config, game_id, mod_name)
    if package_root.exists():
        msg = f"Destination already exists: {package_root}"
        raise LibraryError(msg)
    package_root.mkdir(parents=True, exist_ok=True)
    download_path, file_action = _store_download_file(
        source,
        package_root,
        copy=copy,
    )
    if is_archive(download_path):
        try:
            extract_archive(download_path, package_root)
        except ArchiveError as exc:
            shutil.rmtree(package_root)
            raise LibraryError(str(exc)) from exc
        action = ImportAction.EXTRACTED
    elif is_loose_download(download_path):
        deploy_file = package_root / download_path.name
        if not deploy_file.exists():
            shutil.copy2(download_path, deploy_file)
        action = (
            ImportAction.EXTRACTED
            if file_action == ImportAction.COPIED
            else ImportAction.MOVED
        )
    else:
        action = file_action
    return package_root, download_path, action


def _register_package_dir(
    config: Config,
    package_root: Path,
    game_id: str,
    *,
    name: str | None,
) -> tuple[ModRecord, ImportAction]:
    mod_name = validate_path_segment(name or package_root.name, field="mod name")
    library_root = config.library_root.resolve()
    game_dir = game_library_dir(config, game_id)
    if not path_within_root(package_root, library_root):
        msg = (
            f"Mod package must already live under the library: {package_root} "
            f"(expected under {game_dir})"
        )
        raise LibraryError(msg)
    if not path_within_root(package_root, game_dir):
        msg = (
            f"Mod package is in the library but not under this game's directory: "
            f"{package_root} (expected under {game_dir})"
        )
        raise LibraryError(msg)
    if not _is_valid_package_dir(package_root):
        msg = (
            f"Not a valid mod package (expected {DOWNLOAD_DIRNAME}/ plus extracted "
            f"content): {package_root}"
        )
        raise LibraryError(msg)
    download_path = _resolve_download_path(package_root)
    if download_path is None:
        msg = f"Package download/ must contain exactly one file: {package_root}"
        raise LibraryError(msg)
    record = ModRecord(
        name=mod_name,
        game=game_id,
        source_path=package_root.resolve(),
        download_path=download_path.resolve(),
    )
    return record, ImportAction.REGISTERED


def import_mods_from_directory(
    config: Config,
    state: State,
    parent: Path,
    game_id: str,
    *,
    copy: bool = True,
    dry_run: bool = False,
) -> tuple[State, list[ImportResult], list[ImportFailure], list[ImportSkip]]:
    if game_id not in config.games:
        msg = f"Unknown game profile: {game_id}"
        raise LibraryError(msg)

    parent = parent.resolve()
    if not parent.exists():
        msg = f"Mod path does not exist: {parent}"
        raise LibraryError(msg)
    if not parent.is_dir():
        msg = f"Mod path is not a directory: {parent}"
        raise LibraryError(msg)

    updated = state.model_copy(deep=True)
    results: list[ImportResult] = []
    failures: list[ImportFailure] = []
    skips: list[ImportSkip] = []

    for entry in sorted(parent.iterdir(), key=lambda path: path.name):
        if entry.name.startswith("."):
            skips.append(ImportSkip(path=entry, reason="hidden"))
            continue
        if entry.is_dir():
            skips.append(ImportSkip(path=entry, reason="not_a_download_file"))
            continue
        if not entry.is_file():
            continue
        if not is_download_file(entry):
            skips.append(ImportSkip(path=entry, reason="unsupported_file"))
            continue

        try:
            mod_name = _derive_mod_name(entry, None)
        except PathValidationError as exc:
            failures.append(ImportFailure(name=entry.name, error=str(exc)))
            continue

        if _mod_registered(updated, game_id, mod_name):
            skips.append(ImportSkip(path=entry, reason="already_registered"))
            continue

        if dry_run:
            try:
                package_root = resolve_mod_destination(config, game_id, mod_name)
                if package_root.exists():
                    raise LibraryError(f"Destination already exists: {package_root}")
                record = ModRecord(
                    name=mod_name,
                    game=game_id,
                    source_path=package_root,
                    download_path=package_root / DOWNLOAD_DIRNAME / entry.name,
                )
                action = (
                    ImportAction.EXTRACTED if is_archive(entry) else ImportAction.COPIED
                )
                if not copy:
                    action = ImportAction.MOVED
            except (LibraryError, PathValidationError, ValueError) as exc:
                failures.append(ImportFailure(name=mod_name, error=str(exc)))
                continue
            results.append(ImportResult(record=record, action=action))
            continue

        try:
            updated, record, action = import_mod(
                config,
                updated,
                entry,
                game_id=game_id,
                name=mod_name,
                copy=copy,
            )
        except (LibraryError, ValueError) as exc:
            failures.append(ImportFailure(name=mod_name, error=str(exc)))
            continue
        results.append(ImportResult(record=record, action=action))

    if dry_run:
        return state, results, failures, skips
    return updated, results, failures, skips


def game_library_dir(config: Config, game_id: str) -> Path:
    profile = config.games.get(game_id)
    if profile is None:
        msg = f"Unknown game profile: {game_id}"
        raise LibraryError(msg)
    if profile.library_subpath:
        return resolve_under_root(
            config.library_root,
            profile.library_subpath,
        )
    return resolve_under_root(config.library_root, game_id)


def resolve_mod_destination(
    config: Config,
    game_id: str,
    name: str,
) -> Path:
    validated_name = validate_path_segment(name, field="mod name")
    return resolve_under_root(game_library_dir(config, game_id), validated_name)


def resolve_mod_source(config: Config, game_id: str, name_or_path: Path) -> Path:
    """Resolve bare mod name to game_library_dir/name when that directory exists."""
    arg = str(name_or_path)
    if (
        not name_or_path.is_absolute()
        and "/" not in arg
        and "\\" not in arg
        and game_id in config.games
    ):
        try:
            candidate = resolve_mod_destination(config, game_id, arg)
            if candidate.is_dir():
                return candidate.resolve()
        except PathValidationError as exc:
            raise LibraryError(str(exc)) from exc
    return name_or_path.resolve()


def import_mod(
    config: Config,
    state: State,
    source: Path,
    *,
    game_id: str,
    name: str | None = None,
    nexus_mod_id: int | None = None,
    target: int | str | None = None,
    copy: bool = True,
) -> tuple[State, ModRecord, ImportAction]:
    if game_id not in config.games:
        msg = f"Unknown game profile: {game_id}"
        raise LibraryError(msg)

    source = source.resolve()
    if not source.exists():
        msg = f"Mod path does not exist: {source}"
        raise LibraryError(msg)

    try:
        if source.is_file():
            if not is_download_file(source):
                msg = (
                    f"Unsupported download file type: {source.suffix or source.name}. "
                    "Import a Nexus archive (.zip, .7z, .rar) or supported loose file."
                )
                raise LibraryError(msg)
            package_root, download_path, action = _import_file_package(
                config,
                source,
                game_id,
                name=name,
                copy=copy,
            )
            mod_name = package_root.name
        elif source.is_dir():
            library_root = config.library_root.resolve()
            if path_within_root(source, library_root):
                record, action = _register_package_dir(
                    config,
                    source,
                    game_id,
                    name=name,
                )
                mod_name = record.name
                package_root = record.source_path
                download_path = record.download_path
            else:
                msg = (
                    "Directory import requires an existing mod package in the library. "
                    "Import the original Nexus download file instead: "
                    "lmm add mod.zip --game <id>"
                )
                raise LibraryError(msg)
        else:
            msg = f"Mod path is not a file or directory: {source}"
            raise LibraryError(msg)
    except PathValidationError as exc:
        raise LibraryError(str(exc)) from exc

    record = ModRecord(
        name=mod_name,
        game=game_id,
        source_path=package_root,
        download_path=download_path,
        nexus_mod_id=nexus_mod_id,
        target=target,
    )
    updated_state = add_mod_record(state, record)
    return updated_state, record, action


def list_mods(state: State, game_id: str | None = None) -> list[ModRecord]:
    if game_id is None:
        mods = list(state.mods)
    else:
        mods = [mod for mod in state.mods if mod.game == game_id]
    return sorted(mods, key=lambda mod: (mod.game, mod.name))


def mod_is_deployed(mod: ModRecord) -> bool:
    return len(mod.deployed_links) > 0
