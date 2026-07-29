"""Import mods into the library and list registered mods."""

from __future__ import annotations

import enum
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lmm.archive import (
    DOWNLOAD_DIRNAME,
    ArchiveError,
    extract_archive,
    is_archive,
    is_download_file,
    is_loose_download,
    parse_nexus_download_filename,
    peek_archive_root_name,
)
from lmm.config import Config
from lmm.nexus.updates_hash import _file_md5
from lmm.paths import (
    PathValidationError,
    path_within_root,
    resolve_under_root,
    validate_path_segment,
)
from lmm.state import ModRecord, State, add_mod_record, update_mod_record


class LibraryError(Exception):
    """Raised when a library operation fails."""


def _unknown_game_message(game_id: str) -> str:
    return f"Unknown game profile: {game_id}. Run `lmm game list` or `lmm game add`."


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


def _replace_download_file(
    source: Path,
    package_root: Path,
    *,
    copy: bool,
) -> tuple[Path, UpdateAction]:
    download_dir = package_root / DOWNLOAD_DIRNAME
    download_dir.mkdir(parents=True, exist_ok=True)
    for entry in download_dir.iterdir():
        if entry.is_file() and not entry.name.startswith("."):
            entry.unlink()
    destination = download_dir / source.name
    if copy:
        shutil.copy2(source, destination)
        return destination, UpdateAction.COPIED
    shutil.move(str(source), str(destination))
    return destination, UpdateAction.MOVED


def _populate_package_from_download(
    download_path: Path,
    package_root: Path,
) -> None:
    if is_archive(download_path):
        try:
            extract_archive(download_path, package_root)
        except ArchiveError as exc:
            raise LibraryError(str(exc)) from exc
        return
    if is_loose_download(download_path):
        deploy_file = package_root / download_path.name
        shutil.copy2(download_path, deploy_file)
        return
    msg = (
        f"Unsupported download file type: {download_path.suffix or download_path.name}"
    )
    raise LibraryError(msg)


def _import_file_package(
    config: Config,
    source: Path,
    game_id: str,
    *,
    name: str | None,
    copy: bool,
    dry_run: bool = False,
) -> tuple[Path, Path, ImportAction]:
    mod_name = _derive_mod_name(source, name)
    package_root = resolve_mod_destination(config, game_id, mod_name)
    if package_root.exists():
        msg = f"Destination already exists: {package_root}"
        raise LibraryError(msg)
    download_path = package_root / DOWNLOAD_DIRNAME / source.name
    if dry_run:
        action = (
            ImportAction.EXTRACTED
            if is_archive(source) or is_loose_download(source)
            else (ImportAction.COPIED if copy else ImportAction.MOVED)
        )
        return package_root, download_path, action
    package_root.mkdir(parents=True, exist_ok=True)
    download_path, file_action = _store_download_file(
        source,
        package_root,
        copy=copy,
    )
    if is_archive(download_path):
        _populate_package_from_download(download_path, package_root)
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
        raise LibraryError(_unknown_game_message(game_id))

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
        raise LibraryError(_unknown_game_message(game_id))
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
    dry_run: bool = False,
) -> tuple[State, ModRecord, ImportAction]:
    if game_id not in config.games:
        raise LibraryError(_unknown_game_message(game_id))

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
                dry_run=dry_run,
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
    if dry_run:
        return state, record, action
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


class UpdateAction(enum.StrEnum):
    UPDATED = "updated"
    COPIED = "copied"
    MOVED = "moved"
    EXTRACTED = "extracted"


@dataclass
class UpdateSkip:
    path: Path
    reason: str


@dataclass
class UpdateFailure:
    name: str
    error: str


@dataclass
class UpdateResult:
    record: ModRecord
    action: UpdateAction


def _mods_for_game(state: State, game_id: str) -> list[ModRecord]:
    return [mod for mod in state.mods if mod.game == game_id]


def _find_mod_by_name(state: State, game_id: str, name: str) -> ModRecord | None:
    matches = [mod for mod in _mods_for_game(state, game_id) if mod.name == name]
    if len(matches) == 1:
        return matches[0]
    return None


def _match_download_file_to_mod(
    state: State,
    game_id: str,
    source: Path,
) -> ModRecord | list[ModRecord] | None:
    mods = _mods_for_game(state, game_id)
    try:
        mod_name = _derive_mod_name(source, None)
    except PathValidationError:
        return None

    name_matches = [mod for mod in mods if mod.name == mod_name]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        return name_matches

    nexus_id = parse_nexus_download_filename(source)
    if nexus_id is not None:
        id_matches = [mod for mod in mods if mod.nexus_mod_id == nexus_id]
        if len(id_matches) == 1:
            return id_matches[0]
        if len(id_matches) > 1:
            return id_matches
    return None


def _download_is_current(mod: ModRecord, source: Path) -> bool:
    new_md5 = _file_md5(source)
    if mod.file_md5 and mod.file_md5.lower() == new_md5.lower():
        return True
    if mod.download_path is not None and mod.download_path.is_file():
        return _file_md5(mod.download_path).lower() == new_md5.lower()
    return False


def _build_package_staging(
    source: Path,
    staging_root: Path,
    *,
    copy: bool,
) -> tuple[Path, UpdateAction]:
    """Stage a package from a download file without touching the live package."""
    staging_root.mkdir(parents=True, exist_ok=True)
    stored_path, file_action = _replace_download_file(
        source,
        staging_root,
        copy=copy,
    )
    _populate_package_from_download(stored_path, staging_root)
    action = (
        UpdateAction.EXTRACTED
        if is_archive(stored_path) or is_loose_download(stored_path)
        else UpdateAction(file_action.value)
    )
    return stored_path, action


def _swap_package_root(package_root: Path, staging_root: Path) -> None:
    """Atomically replace package_root with staging_root; restore on failure."""
    parent = package_root.parent
    backup = Path(
        tempfile.mkdtemp(prefix=f".{package_root.name}.bak-", dir=parent),
    )
    # mkdtemp creates an empty dir; remove so rename can use the name.
    backup.rmdir()
    try:
        package_root.rename(backup)
    except OSError as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        msg = f"Failed to backup mod package for update: {package_root}: {exc}"
        raise LibraryError(msg) from exc
    try:
        staging_root.rename(package_root)
    except OSError as exc:
        try:
            backup.rename(package_root)
        except OSError:
            pass
        shutil.rmtree(staging_root, ignore_errors=True)
        msg = f"Failed to install updated mod package: {package_root}: {exc}"
        raise LibraryError(msg) from exc
    shutil.rmtree(backup, ignore_errors=True)


def refresh_mod_package(
    mod: ModRecord,
    source: Path,
    *,
    copy: bool = True,
    dry_run: bool = False,
) -> tuple[ModRecord, UpdateAction]:
    source = source.resolve()
    if not source.is_file():
        msg = f"Download path is not a file: {source}"
        raise LibraryError(msg)
    if not is_download_file(source):
        msg = (
            f"Unsupported download file type: {source.suffix or source.name}. "
            "Use a Nexus archive (.zip, .7z, .rar) or supported loose file."
        )
        raise LibraryError(msg)
    if _download_is_current(mod, source):
        msg = "Download file is identical to the installed package"
        raise LibraryError(msg)

    package_root = mod.source_path.resolve()
    if not package_root.is_dir():
        msg = f"Mod package does not exist: {package_root}"
        raise LibraryError(msg)

    download_path = package_root / DOWNLOAD_DIRNAME / source.name
    file_md5 = _file_md5(source)
    if dry_run:
        return (
            mod.model_copy(
                update={
                    "download_path": download_path,
                    "file_md5": file_md5,
                    "update_available": False,
                }
            ),
            UpdateAction.EXTRACTED if is_archive(source) else UpdateAction.COPIED,
        )

    parent = package_root.parent
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".{package_root.name}.new-", dir=parent),
    )
    try:
        stored_path, action = _build_package_staging(
            source,
            staging_root,
            copy=copy,
        )
    except (LibraryError, ArchiveError, OSError):
        shutil.rmtree(staging_root, ignore_errors=True)
        raise
    # After staging succeeds, swap into place (package untouched until rename).
    final_download = package_root / DOWNLOAD_DIRNAME / stored_path.name
    _swap_package_root(package_root, staging_root)
    return (
        mod.model_copy(
            update={
                "download_path": final_download.resolve(),
                "file_md5": file_md5,
                "update_available": False,
            }
        ),
        action,
    )


def apply_nexus_metadata_after_update(
    config: Config,
    mod: ModRecord,
    *,
    client: object | None = None,
) -> ModRecord:
    if client is None or mod.nexus_mod_id is None:
        return mod
    from lmm.nexus.client import NexusClient
    from lmm.nexus.link import LinkError, link_mod_record

    if not isinstance(client, NexusClient):
        return mod
    try:
        linked = link_mod_record(
            config,
            mod,
            client=client,
            mod_id=mod.nexus_mod_id,
        )
    except LinkError:
        return mod
    return linked.model_copy(
        update={
            "update_available": False,
            "latest_version": linked.installed_version or mod.latest_version,
        }
    )


def update_mod(
    config: Config,
    state: State,
    mod: ModRecord,
    source: Path,
    *,
    copy: bool = True,
    dry_run: bool = False,
    client: object | None = None,
) -> tuple[State, ModRecord, UpdateAction]:
    if mod.game not in config.games:
        raise LibraryError(_unknown_game_message(mod.game))

    current_mod = _find_mod_by_name(state, mod.game, mod.name)
    if current_mod is None:
        msg = f"Mod not registered: {mod.game}/{mod.name}"
        raise LibraryError(msg)

    source = source.resolve()
    if _download_is_current(current_mod, source):
        msg = "Download file is identical to the installed package"
        raise LibraryError(msg)

    updated_state = state
    if current_mod.deployed_links and not dry_run:
        from lmm.deploy import undeploy_mod

        updated_state, _ = undeploy_mod(
            config,
            updated_state,
            current_mod,
            dry_run=False,
        )
        refreshed = _find_mod_by_name(updated_state, mod.game, mod.name)
        if refreshed is None:
            msg = f"Mod not found after undeploy: {mod.game}/{mod.name}"
            raise LibraryError(msg)
        current_mod = refreshed

    record, action = refresh_mod_package(
        current_mod,
        source,
        copy=copy,
        dry_run=dry_run,
    )
    if not dry_run:
        record = apply_nexus_metadata_after_update(config, record, client=client)

    if dry_run:
        return state, record, action
    updated_state = update_mod_record(updated_state, record)
    return updated_state, record, action


def update_mods_from_directory(
    config: Config,
    state: State,
    parent: Path,
    game_id: str,
    *,
    copy: bool = True,
    only_updates: bool = False,
    dry_run: bool = False,
    client: object | None = None,
) -> tuple[State, list[UpdateResult], list[UpdateFailure], list[UpdateSkip]]:
    if game_id not in config.games:
        raise LibraryError(_unknown_game_message(game_id))

    parent = parent.resolve()
    if not parent.exists():
        msg = f"Mod path does not exist: {parent}"
        raise LibraryError(msg)
    if not parent.is_dir():
        msg = f"Mod path is not a directory: {parent}"
        raise LibraryError(msg)

    updated = state.model_copy(deep=True)
    results: list[UpdateResult] = []
    failures: list[UpdateFailure] = []
    skips: list[UpdateSkip] = []

    for entry in sorted(parent.iterdir(), key=lambda path: path.name):
        if entry.name.startswith("."):
            skips.append(UpdateSkip(path=entry, reason="hidden"))
            continue
        if entry.is_dir():
            skips.append(UpdateSkip(path=entry, reason="not_a_download_file"))
            continue
        if not entry.is_file():
            continue
        if not is_download_file(entry):
            skips.append(UpdateSkip(path=entry, reason="unsupported_file"))
            continue

        match = _match_download_file_to_mod(updated, game_id, entry)
        if match is None:
            skips.append(UpdateSkip(path=entry, reason="not_registered"))
            continue
        if isinstance(match, list):
            skips.append(UpdateSkip(path=entry, reason="ambiguous_match"))
            continue

        if only_updates and not match.update_available:
            skips.append(UpdateSkip(path=entry, reason="not_flagged"))
            continue
        if _download_is_current(match, entry):
            skips.append(UpdateSkip(path=entry, reason="already_current"))
            continue

        try:
            updated, record, action = update_mod(
                config,
                updated,
                match,
                entry,
                copy=copy,
                dry_run=dry_run,
                client=client,
            )
        except (LibraryError, ValueError) as exc:
            failures.append(UpdateFailure(name=match.name, error=str(exc)))
            continue
        results.append(UpdateResult(record=record, action=action))

    if dry_run:
        return state, results, failures, skips
    return updated, results, failures, skips
