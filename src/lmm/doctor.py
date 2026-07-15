"""Setup validation checks for lmm doctor."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from lmm.config import ConfigError, ConfigStore
from lmm.deploy import DeployError, resolve_deploy_target
from lmm.library import mod_is_deployed
from lmm.state import StateStore

CheckStatus = Literal["ok", "warning", "error", "info"]


@dataclass
class DoctorCheck:
    name: str
    status: CheckStatus
    message: str


def run_doctor(
    config_store: ConfigStore,
    state_store: StateStore,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []

    try:
        config = config_store.load()
        checks.append(
            DoctorCheck(name="config", status="ok", message="config.toml loaded"),
        )
    except ConfigError as exc:
        checks.append(
            DoctorCheck(name="config", status="error", message=str(exc)),
        )
        return checks

    state = state_store.load()

    env_root = os.environ.get("LMM_LIBRARY_ROOT", "").strip()
    if env_root:
        checks.append(
            DoctorCheck(
                name="library_root_env",
                status="info",
                message=f"LMM_LIBRARY_ROOT overrides config: {env_root}",
            ),
        )

    library_root = config_store.resolve_library_root(config)
    if library_root.exists():
        if os.access(library_root, os.W_OK):
            checks.append(
                DoctorCheck(
                    name="library_root",
                    status="ok",
                    message=f"library_root exists and is writable: {library_root}",
                ),
            )
        else:
            checks.append(
                DoctorCheck(
                    name="library_root",
                    status="error",
                    message=f"library_root is not writable: {library_root}",
                ),
            )
    else:
        checks.append(
            DoctorCheck(
                name="library_root",
                status="error",
                message=f"library_root does not exist: {library_root}",
            ),
        )

    for game_id, profile in sorted(config.games.items()):
        for index, target in enumerate(profile.targets):
            if target.exists():
                checks.append(
                    DoctorCheck(
                        name=f"target.{game_id}.{index}",
                        status="ok",
                        message=f"Deploy target [{index}] exists: {target}",
                    ),
                )
            else:
                checks.append(
                    DoctorCheck(
                        name=f"target.{game_id}.{index}",
                        status="warning",
                        message=f"Deploy target [{index}] missing: {target}",
                    ),
                )

    for mod in state.mods:
        source = mod.source_path
        if source.exists():
            checks.append(
                DoctorCheck(
                    name=f"mod.{mod.game}/{mod.name}",
                    status="ok",
                    message=f"Mod source exists: {source}",
                ),
            )
        else:
            checks.append(
                DoctorCheck(
                    name=f"mod.{mod.game}/{mod.name}",
                    status="warning",
                    message=f"Mod source missing: {source}",
                ),
            )
        if mod.enabled and not mod_is_deployed(mod):
            checks.append(
                DoctorCheck(
                    name=f"mod.{mod.game}/{mod.name}.deploy",
                    status="info",
                    message=(
                        f"Mod {mod.game}/{mod.name} is enabled but not deployed; "
                        f"run 'lmm deploy {mod.game}'"
                    ),
                ),
            )

        profile = config.games.get(mod.game)
        if profile is None:
            continue

        if profile.deploy_layout == "mod_subdir" and mod.deployed_links:
            try:
                deploy_target = resolve_deploy_target(config, mod)
            except DeployError:
                continue
            for deployed in mod.deployed_links:
                link_path = deployed.link
                if link_path.parent.resolve() == deploy_target.resolve():
                    checks.append(
                        DoctorCheck(
                            name=f"mod.{mod.game}/{mod.name}.layout",
                            status="warning",
                            message=(
                                f"Mod {mod.game}/{mod.name} has links directly under "
                                f"{deploy_target} but deploy_layout is mod_subdir "
                                f"(expected {deploy_target / mod.name}/...); "
                                f"run 'lmm undeploy {mod.game} --yes' then "
                                f"'lmm deploy {mod.game}'"
                            ),
                        ),
                    )
                    break

        if profile.deploy_layout == "mirror" and mod.source_path.is_dir():
            has_subdir = any(entry.is_dir() for entry in mod.source_path.iterdir())
            if not has_subdir:
                checks.append(
                    DoctorCheck(
                        name=f"mod.{mod.game}/{mod.name}.layout",
                        status="warning",
                        message=(
                            f"Mod {mod.game}/{mod.name} source has no subdirectories; "
                            f"mirror layout expects game-relative paths inside the "
                            f"mod folder (e.g. Content/Paks/~mods/...)"
                        ),
                    ),
                )

    api_key = config_store.resolve_api_key(config)
    if api_key:
        checks.append(
            DoctorCheck(
                name="nexus_api_key",
                status="ok",
                message="Nexus API key configured",
            ),
        )
    else:
        checks.append(
            DoctorCheck(
                name="nexus_api_key",
                status="info",
                message="Nexus API key not set (identify/check unavailable)",
            ),
        )

    return checks


def doctor_has_errors(checks: list[DoctorCheck]) -> bool:
    return any(check.status == "error" for check in checks)
