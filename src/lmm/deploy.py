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


def _unknown_game_message(game_id: str) -> str:
    return f"Unknown game profile: {game_id}. Run `lmm game list` or `lmm game add`."


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
        raise DeployError(_unknown_game_message(mod.game))
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
        raise DeployError(_unknown_game_message(game_id))

    plan: list[PlannedLink] = []
    for mod in state.mods:
        if mod.game != game_id or not mod.enabled:
            continue
        plan.extend(build_link_plan_for_mod(config, mod))
    return plan


def build_link_plan_for_mod(config: Config, mod: ModRecord) -> list[PlannedLink]:
    profile = config.games.get(mod.game)
    if profile is None:
        raise DeployError(_unknown_game_message(mod.game))

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


def _lexists(path: Path) -> bool:
    """True when path exists on disk or is a dangling symlink."""
    try:
        path.lstat()
    except (FileNotFoundError, OSError):
        return False
    return True


def _path_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _link_is_owned_path(link: Path, mod: ModRecord) -> bool:
    for deployed in mod.deployed_links:
        if deployed.link == link:
            return True
        try:
            if deployed.link.resolve() == link.resolve():
                return True
        except OSError:
            if deployed.link.as_posix() == link.as_posix():
                return True
    return False


def _mkdir_parents(
    link: Path,
    created_dirs: list[Path],
    *,
    dry_run: bool,
    deploy_target: Path | None = None,
) -> None:
    parent = link.parent
    if _lexists(parent) or parent == parent.parent:
        if (
            deploy_target is not None
            and parent.is_symlink()
            and not _path_under_root(parent, deploy_target)
        ):
            msg = f"Refusing to deploy through symlink outside target: {parent}"
            raise DeployError(msg)
        return
    ancestors: list[Path] = []
    current = parent
    while not _lexists(current) and current != current.parent:
        ancestors.append(current)
        current = current.parent
    if (
        deploy_target is not None
        and _lexists(current)
        and current.is_symlink()
        and not _path_under_root(current, deploy_target)
    ):
        msg = f"Refusing to deploy through symlink outside target: {current}"
        raise DeployError(msg)
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
        if not _lexists(link):
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
        # Dangling owned symlink: still remove (never delete real files).
        if not link.exists():
            if not dry_run:
                link.unlink(missing_ok=True)
            result.links_removed += 1
            current.deployed_links = [
                item for item in current.deployed_links if item.link != deployed.link
            ]
            result.warnings.append(f"Removed dangling symlink: {link}")
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


def _apply_link(
    mod: ModRecord,
    link: Path,
    source: Path,
    outcome: DeployOutcome,
    *,
    dry_run: bool,
    deploy_target: Path,
) -> ModRecord:
    """Create one planned symlink or record conflict/skip. Mutates outcome."""
    if _lexists(link):
        if link.is_symlink() and not link.exists():
            if _link_is_owned_path(link, mod):
                if not dry_run:
                    link.unlink(missing_ok=True)
                mod.deployed_links = [
                    item for item in mod.deployed_links if item.link != link
                ]
                # Fall through to recreate.
            else:
                outcome.conflicts.append(
                    f"Conflict at {link}: dangling foreign symlink",
                )
                return mod
        elif link.is_symlink():
            try:
                same_target = link.resolve() == source.resolve()
            except OSError:
                same_target = False
            if same_target and _owned_link(link, source, mod):
                outcome.links_skipped += 1
                return mod
            outcome.conflicts.append(
                f"Conflict at {link}: foreign symlink (not owned by lmm)",
            )
            return mod
        else:
            outcome.conflicts.append(
                f"Conflict at {link}: foreign file blocks symlink",
            )
            return mod

    created_dirs = list(mod.created_dirs)
    if dry_run:
        outcome.links_created += 1
        mod.deployed_links.append(DeployedLink(link=link, source=source))
        _mkdir_parents(link, created_dirs, dry_run=True, deploy_target=deploy_target)
        mod.created_dirs = created_dirs
        return mod

    _mkdir_parents(link, created_dirs, dry_run=False, deploy_target=deploy_target)
    os.symlink(source, link)
    mod.deployed_links.append(DeployedLink(link=link, source=source))
    mod.created_dirs = created_dirs
    outcome.links_created += 1
    return mod


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

    # Rebuild plan from updated (post-disable-undeploy) state view for enabled mods.
    plan_state = state.model_copy(
        update={"mods": [updated_mods[(m.game, m.name)] for m in state.mods]}
    )
    plan = build_link_plan(config, plan_state, game_id)

    for entry in plan:
        mod_key = (entry.mod.game, entry.mod.name)
        mod = updated_mods[mod_key]
        deploy_target = resolve_deploy_target(config, mod)
        updated_mods[mod_key] = _apply_link(
            mod,
            entry.link,
            entry.source,
            outcome,
            dry_run=dry_run,
            deploy_target=deploy_target,
        )

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
    deploy_target = resolve_deploy_target(config, current)

    for entry in plan:
        current = _apply_link(
            current,
            entry.link,
            entry.source,
            outcome,
            dry_run=dry_run,
            deploy_target=deploy_target,
        )

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
        raise DeployError(_unknown_game_message(game_id))

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
