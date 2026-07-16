"""Symlink deploy engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from lmm.archive import DOWNLOAD_DIRNAME
from lmm.config import Config, GameProfile
from lmm.state import DeployedLink, ModRecord, State


class DeployError(Exception):
    """Raised when deploy or undeploy fails."""


@dataclass
class PlannedLink:
    mod: ModRecord
    source: Path
    link: Path


@dataclass
class DeployOutcome:
    links_created: int = 0
    links_removed: int = 0
    links_skipped: int = 0
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False


@dataclass
class UndeployOutcome:
    links_removed: int = 0
    links_skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False


@dataclass
class RemoveModOutcome:
    mod_ref: str
    links_removed: int = 0
    links_skipped: int = 0
    deleted_files: bool = False
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False


@dataclass
class LinkRemovalResult:
    mod: ModRecord
    links_removed: int = 0
    links_skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def resolve_deploy_target(config: Config, mod: ModRecord) -> Path:
    """Resolve deploy directory: default targets[0], or per-mod override."""
    profile = config.games.get(mod.game)
    if profile is None:
        msg = f"Unknown game profile: {mod.game}"
        raise DeployError(msg)
    if not profile.targets:
        msg = f"Game profile {mod.game} has no deploy targets configured"
        raise DeployError(msg)

    if mod.target is None:
        return profile.targets[0].resolve()
    if isinstance(mod.target, int):
        if mod.target < 0 or mod.target >= len(profile.targets):
            msg = (
                f"Deploy target index {mod.target} out of range for game "
                f"{mod.game} (0-{len(profile.targets) - 1})"
            )
            raise DeployError(msg)
        return profile.targets[mod.target].resolve()
    return Path(mod.target).resolve()


def _owned_link(link: Path, source: Path, mod: ModRecord) -> bool:
    resolved_link = link
    resolved_source = source.resolve()
    for deployed in mod.deployed_links:
        link_matches = (
            deployed.link == resolved_link
            or deployed.link.resolve() == resolved_link.resolve()
        )
        if link_matches:
            return deployed.source.resolve() == resolved_source
    return False


def resolve_link_path(
    profile: GameProfile,
    mod: ModRecord,
    deploy_target: Path,
    source_file: Path,
    source_root: Path,
) -> Path:
    """Compute symlink destination from game deploy layout and mod source."""
    relative = source_file.relative_to(source_root)
    if profile.deploy_layout == "mod_subdir":
        return deploy_target / mod.name / relative
    return deploy_target / relative


def build_link_plan(config: Config, state: State, game_id: str) -> list[PlannedLink]:
    if game_id not in config.games:
        msg = f"Unknown game profile: {game_id}"
        raise DeployError(msg)

    plan: list[PlannedLink] = []
    for mod in state.mods:
        if mod.game != game_id or not mod.enabled:
            continue
        plan.extend(build_link_plan_for_mod(config, mod))
    return plan


def build_link_plan_for_mod(config: Config, mod: ModRecord) -> list[PlannedLink]:
    profile = config.games.get(mod.game)
    if profile is None:
        msg = f"Unknown game profile: {mod.game}"
        raise DeployError(msg)

    deploy_target = resolve_deploy_target(config, mod)
    source_root = mod.source_path.resolve()
    if not source_root.is_dir():
        msg = f"Mod source is not a directory: {source_root}"
        raise DeployError(msg)
    plan: list[PlannedLink] = []
    for source_file in sorted(source_root.rglob("*")):
        if not source_file.is_file():
            continue
        relative = source_file.relative_to(source_root)
        if relative.parts and relative.parts[0] == DOWNLOAD_DIRNAME:
            continue
        link_path = resolve_link_path(
            profile,
            mod,
            deploy_target,
            source_file,
            source_root,
        )
        plan.append(PlannedLink(mod=mod, source=source_file, link=link_path))
    return plan


def _mkdir_parents(link: Path, created_dirs: list[Path], *, dry_run: bool) -> None:
    parent = link.parent
    if parent.exists() or parent == parent.parent:
        return
    ancestors: list[Path] = []
    current = parent
    while not current.exists() and current != current.parent:
        ancestors.append(current)
        current = current.parent
    for directory in reversed(ancestors):
        if directory in created_dirs:
            continue
        if dry_run:
            created_dirs.append(directory)
            continue
        directory.mkdir(exist_ok=True)
        created_dirs.append(directory)


def _cleanup_created_dirs(mod: ModRecord, *, dry_run: bool) -> ModRecord:
    if dry_run:
        return mod.model_copy(update={"created_dirs": []})
    for directory in sorted(
        mod.created_dirs, key=lambda path: len(path.parts), reverse=True
    ):
        if directory.exists() and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return mod.model_copy(update={"created_dirs": []})


def _remove_mod_links(mod: ModRecord, *, dry_run: bool) -> LinkRemovalResult:
    current = mod.model_copy(deep=True)
    result = LinkRemovalResult(mod=current)
    for deployed in list(current.deployed_links):
        link = deployed.link
        source = deployed.source
        if not link.exists():
            result.warnings.append(f"Missing link (already removed): {link}")
            current.deployed_links = [
                item for item in current.deployed_links if item.link != deployed.link
            ]
            result.links_skipped += 1
            continue
        if not link.is_symlink():
            result.warnings.append(f"Not a symlink, skipping: {link}")
            result.links_skipped += 1
            continue
        try:
            if link.resolve() != source.resolve():
                result.warnings.append(f"Symlink target changed, skipping: {link}")
                result.links_skipped += 1
                continue
        except OSError as exc:
            result.warnings.append(f"Cannot read symlink {link}: {exc}")
            result.links_skipped += 1
            continue

        if not dry_run:
            link.unlink()
        result.links_removed += 1
        current.deployed_links = [
            item for item in current.deployed_links if item.link != deployed.link
        ]

    if current.deployed_links:
        result.mod = current
        return result

    result.mod = _cleanup_created_dirs(current, dry_run=dry_run)
    return result


def deploy_game(
    config: Config,
    state: State,
    game_id: str,
    *,
    dry_run: bool = False,
) -> tuple[State, DeployOutcome]:
    outcome = DeployOutcome(dry_run=dry_run)
    updated_mods: dict[tuple[str, str], ModRecord] = {
        (mod.game, mod.name): mod.model_copy(deep=True) for mod in state.mods
    }

    for mod in state.mods:
        if mod.game != game_id or mod.enabled or not mod.deployed_links:
            continue
        mod_key = (mod.game, mod.name)
        removal = _remove_mod_links(updated_mods[mod_key], dry_run=dry_run)
        updated_mods[mod_key] = removal.mod
        outcome.links_removed += removal.links_removed
        outcome.links_skipped += removal.links_skipped
        outcome.warnings.extend(removal.warnings)

    plan = build_link_plan(config, state, game_id)

    for entry in plan:
        mod_key = (entry.mod.game, entry.mod.name)
        mod = updated_mods[mod_key]
        link = entry.link
        source = entry.source

        if link.exists():
            if link.is_symlink() and link.resolve() == source.resolve():
                if _owned_link(link, source, mod):
                    outcome.links_skipped += 1
                    updated_mods[mod_key] = mod
                    continue
                outcome.conflicts.append(
                    f"Conflict at {link}: foreign symlink (not owned by lmm)",
                )
                continue
            if link.is_symlink():
                outcome.conflicts.append(
                    f"Conflict at {link}: foreign symlink (not owned by lmm)",
                )
            else:
                outcome.conflicts.append(
                    f"Conflict at {link}: foreign file blocks symlink",
                )
            continue

        created_dirs = list(mod.created_dirs)
        if dry_run:
            outcome.links_created += 1
            mod.deployed_links.append(DeployedLink(link=link, source=source))
            _mkdir_parents(link, created_dirs, dry_run=True)
            mod.created_dirs = created_dirs
            updated_mods[mod_key] = mod
            continue

        _mkdir_parents(link, created_dirs, dry_run=False)
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(source, link)
        mod.deployed_links.append(DeployedLink(link=link, source=source))
        mod.created_dirs = created_dirs
        updated_mods[mod_key] = mod
        outcome.links_created += 1

    if dry_run:
        return state, outcome

    new_mods = [updated_mods[(mod.game, mod.name)] for mod in state.mods]
    return state.model_copy(update={"mods": new_mods}), outcome


def undeploy_mod(
    config: Config,
    state: State,
    mod: ModRecord,
    *,
    dry_run: bool = False,
) -> tuple[State, LinkRemovalResult]:
    from lmm.state import update_mod_record

    _ = config
    removal = _remove_mod_links(mod, dry_run=dry_run)
    if dry_run:
        return state, removal
    return update_mod_record(state, removal.mod), removal


def deploy_mod(
    config: Config,
    state: State,
    mod: ModRecord,
    *,
    dry_run: bool = False,
) -> tuple[State, DeployOutcome]:
    from lmm.state import update_mod_record

    outcome = DeployOutcome(dry_run=dry_run)
    if not mod.enabled:
        return state, outcome

    current = mod.model_copy(deep=True)
    plan = build_link_plan_for_mod(config, current)

    for entry in plan:
        link = entry.link
        source = entry.source

        if link.exists():
            if link.is_symlink() and link.resolve() == source.resolve():
                if _owned_link(link, source, current):
                    outcome.links_skipped += 1
                    continue
                outcome.conflicts.append(
                    f"Conflict at {link}: foreign symlink (not owned by lmm)",
                )
                continue
            if link.is_symlink():
                outcome.conflicts.append(
                    f"Conflict at {link}: foreign symlink (not owned by lmm)",
                )
            else:
                outcome.conflicts.append(
                    f"Conflict at {link}: foreign file blocks symlink",
                )
            continue

        created_dirs = list(current.created_dirs)
        if dry_run:
            outcome.links_created += 1
            current.deployed_links.append(DeployedLink(link=link, source=source))
            _mkdir_parents(link, created_dirs, dry_run=True)
            current.created_dirs = created_dirs
            continue

        _mkdir_parents(link, created_dirs, dry_run=False)
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(source, link)
        current.deployed_links.append(DeployedLink(link=link, source=source))
        current.created_dirs = created_dirs
        outcome.links_created += 1

    if dry_run:
        return state, outcome
    return update_mod_record(state, current), outcome


def remove_mod(
    config: Config,
    state: State,
    mod: ModRecord,
    *,
    dry_run: bool = False,
    delete_files: bool = False,
) -> tuple[State, RemoveModOutcome]:
    import shutil

    from lmm.paths import path_within_root
    from lmm.state import remove_mod_record

    outcome = RemoveModOutcome(
        mod_ref=f"{mod.game}/{mod.name}",
        dry_run=dry_run,
    )
    removal = _remove_mod_links(mod, dry_run=dry_run)
    outcome.links_removed = removal.links_removed
    outcome.links_skipped = removal.links_skipped
    outcome.warnings.extend(removal.warnings)

    if delete_files:
        library_root = config.library_root.resolve()
        source = mod.source_path.resolve()
        if not path_within_root(source, library_root):
            msg = (
                f"Refusing to delete files outside library_root: {source} "
                f"(library_root={library_root})"
            )
            raise DeployError(msg)
        if source.exists() and not dry_run:
            shutil.rmtree(source)
        outcome.deleted_files = delete_files

    if dry_run:
        return state, outcome

    updated = remove_mod_record(state, mod.game, mod.name)
    return updated, outcome


def undeploy_game(
    config: Config,
    state: State,
    game_id: str,
    *,
    dry_run: bool = False,
) -> tuple[State, UndeployOutcome]:
    if game_id not in config.games:
        msg = f"Unknown game profile: {game_id}"
        raise DeployError(msg)

    outcome = UndeployOutcome(dry_run=dry_run)
    updated_mods: dict[tuple[str, str], ModRecord] = {
        (mod.game, mod.name): mod.model_copy(deep=True) for mod in state.mods
    }

    for mod in state.mods:
        if mod.game != game_id:
            continue
        removal = _remove_mod_links(updated_mods[(mod.game, mod.name)], dry_run=dry_run)
        updated_mods[(mod.game, mod.name)] = removal.mod
        outcome.links_removed += removal.links_removed
        outcome.links_skipped += removal.links_skipped
        outcome.warnings.extend(removal.warnings)

    if dry_run:
        return state, outcome

    new_mods = [updated_mods[(mod.game, mod.name)] for mod in state.mods]
    return state.model_copy(update={"mods": new_mods}), outcome
