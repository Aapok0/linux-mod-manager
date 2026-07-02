# Roadmap

Phased milestones for `lmm`. Build in order; do not start a phase until the prior phase's acceptance criteria pass. Read [SKILL.md](SKILL.md), [architecture.md](architecture.md), and [nexus-api.md](nexus-api.md) first.

## P1 - Foundation

Scope: project skeleton, config + state, library import, listing.

- [x] `pyproject.toml` with `lmm` console entry point and deps (typer, pydantic, httpx, tomli-w, rich).
- [x] `config.py`: load/save `config.toml`, `GameProfile` model, API key resolution (env then file).
- [x] `state.py`: pydantic models, load/save `state.json`, `schema_version`, migration scaffold.
- [x] `library.py`: import a mod dir into `library_root`; resolve `name` and `game/name`.
- [x] CLI: `game add`, `game list`, `add`, `list`.

Acceptance:
- `lmm game add kcd2 --domain kingdomcomedeliverance2 --target <path>` writes a valid profile.
- `lmm add <dir> --game kcd2` records the mod; `lmm list kcd2` shows it.
- Config and state round-trip on disk without data loss.

## P2 - Deploy

Scope: the symlink engine, the core value over plain stow.

- [ ] `deploy.py`: build link plan, conflict detection, create + record links.
- [ ] CLI: `deploy`, `undeploy`, `enable`, `disable`, plus global `--dry-run`.
- [ ] Record `deployed_links` (and created dirs) in state; `undeploy` removes only recorded links.

Acceptance:
- `lmm deploy kcd2` symlinks enabled mods into the target; links appear in state.
- `lmm undeploy kcd2` removes exactly those links and any lmm-created empty dirs; no real game files touched.
- Foreign file at a target path is reported as a conflict and skipped, not overwritten.
- `--dry-run` prints the plan and changes nothing.
- Re-running `deploy` is idempotent (no duplicate links, no errors).

## P3 - Nexus integration

Scope: version checking and identification (check-only).

- [ ] `nexus/client.py`: v1 client, `apikey` header, retry/backoff, rate-limit accounting, disk cache.
- [ ] Key validation via `users/validate.json`.
- [ ] `nexus/updates.py`: version compare; `identify` via `md5_search`.
- [ ] CLI: `check`, `identify`.

Acceptance:
- Invalid/missing key yields a clear error; valid key validates.
- `lmm identify kcd2` fills `nexus_mod_id`/`file_id`/`installed_version` for matchable mods.
- `lmm check kcd2` reports mods with updates (installed -> latest) and downloads nothing.
- Stays within rate limits using `mods/updated` pre-filter plus cache; backs off on 429.

## P4 - Polish and future

Scope: usability and groundwork for later features.

- [ ] Caching TTLs, `rich` tables/status, structured `--json` output, logging with key masking.
- [ ] State migrations exercised by at least one version bump.
- [ ] README with install + quickstart.

Deferred (design kept open, not implemented now):
- `nxm://` URL handler integration.
- Premium auto-download via `download_link.json`.
- TUI front-end.
- Proton/Wine prefix-aware targets.
- Per-mod, per-subpath routing rules.

## Definition of done (MVP = P1-P3)

A user can register games, import mods, deploy/undeploy them safely via recorded symlinks, and run `lmm check` to learn which installed mods have newer versions on Nexus, all driven by `config.toml` and `state.json`.
