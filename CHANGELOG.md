# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-07-17

First stable release: safety hardening, release hygiene, and documentation aligned with behavior.

### Added

- MIT [`LICENSE`](LICENSE), [`CHANGELOG.md`](CHANGELOG.md), [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Safety model and troubleshooting sections in README
- CI on push and version tags; Python 3.11–3.13 matrix
- Package metadata (license, classifiers, project URLs)
- Recovery hints for unknown games and missing Nexus API keys
- Roadmap phases P5–P7 and updated agent skill docs

### Security

- Zip Slip / unsafe archive member rejection before extract
- Transactional package update (stage then swap) so failed extract does not wipe mods
- Global `--dry-run` honored for config/state mutators without Nexus calls
- Dangling symlink handling on deploy/undeploy
- Clear error when `7z` is missing for `.7z`/`.rar`

### Changed

- Runtime version resolved from package metadata (`importlib.metadata`)
- Dry-run output prefix uses `(dry-run)` so Rich does not swallow brackets
- `list` rejects conflicting positional game vs `--game`

## [0.5.0] - 2026-07-16

### Added

- `lmm update` to refresh installed mod packages from manually downloaded Nexus files
- Bulk update with `--all`, `--only-updates`, and `--no-deploy`
- Automatic redeploy after update unless `--no-deploy`

## [0.4.0] - 2026-07-16

### Added

- Download-first library packages: store Nexus file under `download/`, extract into package root
- State schema v2 (`download_path` field with migration from v1)
- Archive import for `.zip` / `.7z` / `.rar` and supported loose download files
- `lmm mod link` for Nexus metadata by mod id or URL

### Changed

- `lmm add` imports Nexus download files into per-mod packages (replaces directory-tree copy model)

## [0.3.1] - 2026-07-15

### Added

- `deploy_layout` per game profile: `flat`, `mod_subdir`, `mirror` (CryEngine / multi-path games)

### Internal

- Expanded deploy safety tests and CI coverage reporting (PR #6; no version bump)

## [0.3.0] - 2026-07-15

### Added

- `lmm add --all` bulk import of top-level download files from a staging directory

## [0.2.1] - 2026-07-13

### Added

- `lmm doctor` for config, path, layout, and deployment validation
- CLI usability: honest exit codes, lifecycle command hints, improved help text

### Changed

- `undeploy` / `remove` confirmation and non-interactive `--yes` behavior

## [0.2.0] - 2026-07-10

MVP (roadmap P1–P4): register games, import mods, deploy via recorded symlinks, Nexus check-only.

### Added

- Game profiles (`game add`, `game list`, `game target add/list/remove`)
- Library import and `lmm list`
- Symlink deploy engine with conflict detection and `deployed_links` tracking
- `deploy`, `undeploy`, `enable`, `disable`, global `--dry-run`
- Nexus `identify` and `check` (free account, no downloads)
- Rich tables, `--json` output, API key masking in logs
- README quickstart and command reference

### Changed

- Review-driven hardening for deploy reconcile, symlink safety, and Nexus commands (pre-PR era)
- CI: Renovate, CodeQL, SHA-pinned GitHub Actions (PR #3)
- Additional Nexus identify/check hardening (PR #1)

## [0.1.0] - 2026-07-02

### Added

- Project skeleton: `pyproject.toml`, Typer CLI entry point `lmm`
- Config (`config.toml`) and state (`state.json`) with Pydantic models
- Phase 1 library import and listing
- Agent skill docs for architecture and roadmap

[Unreleased]: https://github.com/Aapok0/linux-mod-manager/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Aapok0/linux-mod-manager/compare/v0.5.0...v1.0.0
[0.5.0]: https://github.com/Aapok0/linux-mod-manager/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Aapok0/linux-mod-manager/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Aapok0/linux-mod-manager/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Aapok0/linux-mod-manager/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Aapok0/linux-mod-manager/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Aapok0/linux-mod-manager/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Aapok0/linux-mod-manager/releases/tag/v0.1.0
