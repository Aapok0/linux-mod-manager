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

Scope: symlink engine, default deploy targets, and library UX for the common case.

Design principle: **most mods use the game's default paths; overrides are for exceptions only.**

### Default paths (per game profile)

| Path kind | Game default | Per-mod override |
|-----------|--------------|------------------|
| **Library** (mod storage) | `library_root` + `library_subpath` (or `game_id`) | set implicitly via `source_path` at import |
| **Deploy** (symlink into game) | `targets[0]` — the primary game mod directory | `ModRecord.target`: `null` = default; int = `targets[n]`; str = absolute path |

Register the default deploy dir once on `game add`:

```bash
lmm game add kcd2 --domain kingdomcomedeliverance2 \
  --target "/path/to/game/Mods" \
  --library-subpath "KingdomComeDeliverance2/Mods" \
  --deploy-layout mod_subdir
```

Add extra deploy dirs only when a game needs them (e.g. Oblivion pak + binaries). Most mods never need `--target-index` or `--target-path`.

```bash
lmm game target add oblivion --target "/path/to/game/Data"
lmm game target list oblivion
```

### P2 tasks

- [x] `deploy.py`: `resolve_deploy_target(config, mod) -> Path`; build link plan, conflict detection, create + record links.
- [x] Deploy uses `targets[0]` for every enabled mod unless `mod.target` overrides.
- [x] CLI: `deploy`, `undeploy`, `enable`, `disable`, plus global `--dry-run`.
- [x] CLI: split deploy override flags — `--target-index N` and `--target-path PATH` on `lmm add` (replace ambiguous `--target`).
- [x] CLI: `lmm add <name_or_path> --game <id>` — when arg is a bare mod name (no slashes), resolve to `game_library_dir/<name>` if that directory exists.
- [x] CLI: `game target add`, `game target list`, `game target remove` — manage deploy targets after `game add`.
- [x] Record `deployed_links` (and created dirs) in state; `undeploy` removes only recorded links.

Acceptance:
- `lmm deploy kcd2` symlinks enabled mods into `targets[0]` using `deploy_layout` (KCD2 requires `mod_subdir`: `Mods/<modname>/...`); no per-mod target needed for the common case.
- A mod with `--target-index 1` or `--target-path` deploys only to that override; other mods still use the default.
- `lmm add easysharpening --game kcd2` works when `library_root/.../easysharpening` already exists.
- `lmm undeploy kcd2` removes exactly those links and any lmm-created empty dirs; no real game files touched.
- Foreign file at a target path is reported as a conflict and skipped, not overwritten.
- `--dry-run` prints the plan and changes nothing.
- Re-running `deploy` is idempotent (no duplicate links, no errors).

## P3 - Nexus integration

Scope: version checking and identification (check-only).

- [x] `nexus/client.py`: v1 client, `apikey` header, retry/backoff, rate-limit accounting, disk cache.
- [x] Key validation via `users/validate.json`.
- [x] `nexus/updates.py`: version compare; `identify` via `md5_search`.
- [x] CLI: `check`, `identify`.

Acceptance:
- Invalid/missing key yields a clear error; valid key validates.
- `lmm identify kcd2` fills `nexus_mod_id`/`file_id`/`installed_version` for matchable mods.
- `lmm check kcd2` reports mods with updates (installed -> latest) and downloads nothing.
- Stays within rate limits using `mods/updated` pre-filter plus cache; backs off on 429.

## P4 - Polish (MVP)

Scope: usability and groundwork for later features.

- [x] Caching TTLs, `rich` tables/status, structured `--json` output, logging with key masking.
- [x] State migration scaffold in place.
- [x] README with install + quickstart.

### Definition of done (MVP = P1–P4, v0.2.0)

A user can register games, import mods, deploy/undeploy them safely via recorded symlinks, and run `lmm check` to learn which installed mods have newer versions on Nexus, with polished CLI output, docs, and configuration — all driven by `config.toml` and `state.json`.

## Shipped after MVP (0.3–0.4)

Documented for history; already implemented. Do not re-implement.

- [x] Deploy layouts (`flat` / `mod_subdir` / `mirror`) for CryEngine, UE, and multi-path games.
- [x] Download-first library packages: store Nexus file under `download/`, extract into package root; state `download_path` (schema v2).
- [x] Archive import (`.zip` / `.7z` / `.rar` + loose download suffixes); `add --all` over top-level download files (subdirs skipped).
- [x] `lmm update` (single + bulk `--all` / `--only-updates` / `--no-deploy`).
- [x] `lmm mod link` for Nexus metadata by mod id or URL.
- [x] `lmm doctor` path/layout/deployment validation.
- [x] Game target add/list/remove after registration.

## P5 - Hardening and safety contracts

Scope: correctness and safety gates for v1.0.0. No known data-loss path on normal update/import.

- [x] Archive safety: reject Zip Slip, absolute paths, `..`, and symlink members before extract; clear error when `7z` is missing.
- [x] Transactional update: stage extract then swap (or restore backup) so failed extract never leaves a wiped package.
- [x] Global `--dry-run` honored by every mutator (`game add`, target add/remove, single `add`, enable/disable, `mod link`, update, deploy, undeploy, remove, identify, check); no FS / network / config / state writes; regression tests per mutating command.
- [x] Deploy robustness: dangling symlinks via lstat/`lexists`; contain mkdir through parent symlinks outside deploy target.
- [x] Single version source: `__version__` stays in sync with `pyproject.toml` via `importlib.metadata`.

Acceptance:
- Malicious zip member paths cannot write outside the extract destination.
- Interrupted/failed `update` leaves the previous package intact (or restores it).
- `lmm --dry-run <any mutating command>` changes nothing on disk and makes no Nexus calls.
- Dangling recorded links are cleaned or replaced without `FileExistsError` surprises.
- `lmm --version` matches the packaged version.

## P6 - Project and docs maturity

Scope: open-source / release hygiene for declaring 1.0.

- [x] `LICENSE` (MIT), `CHANGELOG.md`, short `CONTRIBUTING.md`.
- [x] README and `docs/commands.md` match behavior: global flag placement, `--all` download-file semantics, `7z` prerequisite, safety model, troubleshooting, schema v2 state example.
- [x] Skill files (`SKILL.md`, `architecture.md`, this roadmap) stay synchronized with code.
- [x] CI: push to default branch + tags; Python matrix 3.11–3.13; ruff + ty + pytest.
- [x] `pyproject.toml` metadata: license, classifiers, project URLs.

Acceptance:
- New contributor can install, run checks, and understand the workflow from README + CONTRIBUTING.
- Docs do not teach invalid flag placement or wrong `--all` behavior.
- CI covers supported Python versions.

## P7 - Usability and test polish

Scope: modder-facing UX and remaining test gaps preferred before 1.0.

- [x] Recovery-oriented errors (unknown game → `game list`; missing API key → how to set; missing `7z`).
- [x] Document exit-code and `--json` behavior (stabilize current shapes; full envelope unification deferred).
- [x] Tests: CLI `mod link`, update dry-run / failure rollback, Zip Slip, inter-mod deploy collision, dry-run undeploy.
- [x] Reject conflicting `list` positional game vs `--game` when both set and disagree.

Acceptance:
- Common setup mistakes include a next step in the error message.
- Critical safety paths from P5 have direct tests.
- Ambiguous `list` inputs fail clearly instead of silently preferring one form.

## Definition of done (v1.0.0)

A personal Linux user can:

1. Install from the repo with documented deps (including optional `7z` for `.7z`/`.rar`).
2. Register games with the correct `deploy_layout`, and import/update Nexus downloads safely.
3. Deploy/undeploy with recorded-symlink safety and an honest global `--dry-run`.
4. Identify/check updates without API downloads.
5. Trust that docs and skills match behavior; version string is consistent; LICENSE + CHANGELOG are present.
6. See CI green on supported Python versions.

Then bump to **1.0.0** in `pyproject.toml` and `__version__`, and tag the release.

## Deferred (post-1.0)

Design kept open; do not implement as part of v1.0.0:

- `nxm://` URL handler integration.
- Premium auto-download via `download_link.json`.
- TUI front-end.
- Proton/Wine prefix-aware targets.
- Per-mod, per-subpath routing rules.
- Large `cli.py` service-extraction mega-refactor.
- Uniform JSON response envelope rewrite.
- `doctor --fix` auto-repair.
- File locking for concurrent writers.
