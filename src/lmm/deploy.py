"""Symlink deploy engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from lmm.config import Config
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
    links_skipped: int = 0
    conflicts: list[str] = field(default_factory=list)
    dry_run: bool = False


@dataclass
class UndeployOutcome:
    links_removed: int = 0
    links_skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False


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
        if deployed.link == resolved_link or deployed.link.resolve() == resolved_link.resolve():
            return deployed.source.resolve() == resolved_source
    return False


def build_link_plan(config: Config, state: State, game_id: str) -> list[PlannedLink]:
    if game_id not in config.games:
        msg = f"Unknown game profile: {game_id}"
        raise DeployError(msg)

    plan: list[PlannedLink] = []
    for mod in state.mods:
        if mod.game != game_id or not mod.enabled:
            continue
        deploy_target = resolve_deploy_target(config, mod)
        source_root = mod.source_path.resolve()
        if not source_root.is_dir():
            msg = f"Mod source is not a directory: {source_root}"
            raise DeployError(msg)
        for source_file in sorted(source_root.rglob("*")):
            if not source_file.is_file():
                continue
            relative = source_file.relative_to(source_root)
            link_path = deploy_target / relative
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
        if dry_run:
            if directory not in created_dirs:
                created_dirs.append(directory)
            continue
        directory.mkdir(exist_ok=True)
        created_dirs.append(directory)


def deploy_game(
    config: Config,
    state: State,
    game_id: str,
    *,
    dry_run: bool = False,
) -> tuple[State, DeployOutcome]:
    plan = build_link_plan(config, state, game_id)
    outcome = DeployOutcome(dry_run=dry_run)
    updated_mods: dict[tuple[str, str], ModRecord] = {
        (mod.game, mod.name): mod.model_copy(deep=True) for mod in state.mods
    }

    for entry in plan:
        mod_key = (entry.mod.game, entry.mod.name)
        mod = updated_mods[mod_key]
        link = entry.link
        source = entry.source

        if link.exists():
            if link.is_symlink() and link.resolve() == source.resolve():
                if not _owned_link(link, source, mod):
                    mod.deployed_links.append(DeployedLink(link=link, source=source))
                outcome.links_skipped += 1
                updated_mods[mod_key] = mod
                continue
            outcome.conflicts.append(
                f"Conflict at {link}: path exists and is not an lmm-owned symlink",
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
        current = updated_mods[(mod.game, mod.name)]
        for deployed in list(current.deployed_links):
            link = deployed.link
            source = deployed.source
            if not link.exists():
                outcome.warnings.append(f"Missing link (already removed): {link}")
                current.deployed_links = [
                    item
                    for item in current.deployed_links
                    if item.link != deployed.link
                ]
                outcome.links_skipped += 1
                continue
            if not link.is_symlink():
                outcome.warnings.append(f"Not a symlink, skipping: {link}")
                outcome.links_skipped += 1
                continue
            try:
                if link.resolve() != source.resolve():
                    outcome.warnings.append(
                        f"Symlink target changed, skipping: {link}",
                    )
                    outcome.links_skipped += 1
                    continue
            except OSError as exc:
                outcome.warnings.append(f"Cannot read symlink {link}: {exc}")
                outcome.links_skipped += 1
                continue

            if dry_run:
                outcome.links_removed += 1
            else:
                link.unlink()
                outcome.links_removed += 1
            current.deployed_links = [
                item for item in current.deployed_links if item.link != deployed.link
            ]

        if not dry_run:
            for directory in sorted(
                current.created_dirs, key=lambda p: len(p.parts), reverse=True
            ):
                if (
                    directory.exists()
                    and directory.is_dir()
                    and not any(directory.iterdir())
                ):
                    directory.rmdir()
            current.created_dirs = []

        updated_mods[(mod.game, mod.name)] = current

    if dry_run:
        return state, outcome

    new_mods = [updated_mods[(mod.game, mod.name)] for mod in state.mods]
    return state.model_copy(update={"mods": new_mods}), outcome
