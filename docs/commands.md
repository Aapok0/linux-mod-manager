# Command reference

Full `lmm` CLI reference. For a short walkthrough, see [README](../README.md#quickstart).

## Global options

These apply to every subcommand:

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | `~/.config/lmm/config.toml` | Path to `config.toml` |
| `--state PATH` | `~/.local/share/lmm/state.json` | Path to `state.json` |
| `--json` | off | Emit machine-readable JSON instead of tables |
| `--dry-run` | off | Print planned actions without filesystem, network, config, or state writes |
| `--verbose`, `-v` | off | Enable debug logging (API keys are masked) |
| `--version` | — | Print version and exit |

**Global options must precede the subcommand** (e.g. `lmm --dry-run deploy kcd2`, not `lmm deploy --dry-run kcd2`).

`.7z` / `.rar` import and update require system `7z` (p7zip).

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Error, conflicts, identify unmatched mods, or per-mod Nexus failures |

Environment variables respected by defaults:

| Variable | Effect |
|----------|--------|
| `NEXUS_API_KEY` | Nexus API key (overrides `nexus_api_key` in config) |
| `LMM_LIBRARY_ROOT` | Mod library root (overrides `library_root` in config) |
| `XDG_CONFIG_HOME` | Base for default config path |
| `XDG_DATA_HOME` | Base for default library root and state path |
| `XDG_CACHE_HOME` | Base for Nexus response cache |

---

## Game profiles

### `lmm game add <id>`

Register a new game profile.

| Argument / option | Required | Description |
|-------------------|----------|-------------|
| `<id>` | yes | Short game id (e.g. `kcd2`) |
| `--domain` | yes | Nexus game domain (e.g. `kingdomcomedeliverance2`) |
| `--target` | yes | Deploy target path; repeat for multiple. First is the default |
| `--library-subpath` | no | Subpath under `library_root` (default: game id) |
| `--deploy-layout` | no | How mod files map into deploy targets: `flat`, `mod_subdir`, or `mirror` (default: `flat`) |

```bash
lmm game add kcd2 \
  --domain kingdomcomedeliverance2 \
  --target "/path/to/game/Mods" \
  --library-subpath "KingdomComeDeliverance2/Mods" \
  --deploy-layout mod_subdir
```

**Deploy layouts:**

| Layout | Use when |
|--------|----------|
| `flat` | Mod files land directly in the deploy folder (loose paks, single-layer mods) |
| `mod_subdir` | Game expects one subdirectory per mod (`Mods/<name>/`, `~mods/<folder>/`) |
| `mirror` | Mod source tree mirrors game-relative paths; deploy target is the game install root |

See [README — Deploy layouts](../README.md#deploy-layouts) for per-game recipes (KCD2, Stalker 2, Oblivion, Hogwarts Legacy).

### `lmm game list`

List all configured game profiles (id, Nexus domain, deploy layout, deploy targets, library subpath).

---

## Deploy targets

Manage extra deploy directories after `game add`. Index `0` is the primary default and cannot be removed.

### `lmm game target add <id> --target <path>`

Append one or more deploy target paths to a game profile.

### `lmm game target list <id>`

List deploy targets with indices (used by `--target-index` on `add`).

### `lmm game target remove <id> --index <n>`

Remove secondary deploy target(s). Fails if any mod references that index.

---

## Mod library

Each mod is stored as a **package directory** under the game library:

```text
library/<subpath>/<modname>/
  download/<original-nexus-file>.zip
  mod.manifest
  Data/...
```

`lmm add` imports Nexus download files (`.zip`, `.7z`, `.rar`, or supported loose files like `.pak`), stores the original in `download/`, extracts archives into the package root, and records `download_path` for Nexus identify.

### `lmm add <name_or_path> --game <id>`

Import a Nexus download file or register an existing package directory.

| Argument / option | Required | Description |
|-------------------|----------|-------------|
| `<name_or_path>` | yes | Download file, directory of downloads, or library package path |
| `--game` | yes | Game profile id |
| `--name` | no | Mod name (default: inner archive folder or file stem) |
| `--mod-id` | no | Link to Nexus mod id at import time |
| `--mod-url` | no | Link using a Nexus mod page URL at import time |
| `--target-index` | no | Deploy to `targets[n]` instead of default |
| `--target-path` | no | Deploy to an absolute path instead of default |
| `--move` | no | Move download into library instead of copying |
| `--all` | no | Import each top-level download file in a directory |

Use only one of `--target-index` or `--target-path`. With `--all`, do not pass `--name`, `--mod-id`, `--mod-url`, or target overrides.

When `--all` is set, each top-level **download file** becomes a mod. Subdirectories and unsupported files are skipped. Already-registered mods are skipped. Exits **1** only when an import fails. Honors `--dry-run` and `--json`.

```bash
lmm add ~/Downloads/mod.zip --game kcd2
lmm add easysharpening --game kcd2   # existing package in library
lmm add ~/Downloads/kcd2-batch --game kcd2 --all
lmm add mod.zip --game kcd2 --mod-url https://www.nexusmods.com/.../mods/68
```

### `lmm update <mod_or_dir> [download_file] --game <id>`

Apply user-downloaded Nexus files to existing mod packages in place. Preserves mod identity (`nexus_mod_id`, `target`, `enabled`, etc.), refreshes `download/` and the extracted tree, and redeploys updated enabled mods by default.

| Argument / option | Required | Description |
|-------------------|----------|-------------|
| `<mod_or_dir>` | yes | Mod name (single update) or directory of downloads (`--all`) |
| `[download_file]` | single only | Nexus download file for the named mod |
| `--game` | yes | Game profile id |
| `--all` | no | Update each top-level download file in a directory |
| `--only-updates` | no | Skip zips for mods not flagged by `check` |
| `--move` | no | Move download into library instead of copying |
| `--no-deploy` | no | Skip redeploy after updates (default: redeploy) |

Use `--all` with a directory only (no second positional). For a single mod, pass mod name and download file. Exits **1** on failures; skips are non-fatal. Honors `--dry-run` and `--json`.

**Typical workflow:**

```bash
lmm check kcd2
# download flagged mods from Nexus in browser
lmm update ~/Downloads/KingdomComeDeliverance2/ --game kcd2 --all --only-updates
```

**Bulk `--all` behavior (file-driven):** only download files present in the directory are considered. Registered mods with no matching zip in the folder are left untouched (not an error, no skip line).

| File in folder matches registered mod… | `--all` | `--all --only-updates` |
|----------------------------------------|---------|-------------------------|
| `update_available=true` | Apply update | Apply update |
| `update_available=false` | Apply update (unless `already_current`) | Skip (`not_flagged`) |
| Not registered | Skip (`not_registered`) | Skip (`not_registered`) |
| Same MD5 as installed | Skip (`already_current`) | Skip (`already_current`) |

Use `--only-updates` when the download folder may contain stale zips for mods already up to date. Omit it when the folder only contains fresh downloads.

```bash
lmm update easysharpening ~/Downloads/Easy\ Sharpening-68-1-2.zip --game kcd2
lmm update ~/Downloads/KingdomComeDeliverance2/ --game kcd2 --all
lmm update ~/Downloads/KingdomComeDeliverance2/ --game kcd2 --all --only-updates
lmm update ~/Downloads/batch/ --game kcd2 --all --no-deploy
```

Nexus download API not used, since it's a paid feature — you supply files. Run `identify` and `check` first for Nexus metadata and update flags.

### `lmm mod link <mod>`

Manually link a mod to Nexus when automatic identify fails.

| Option | Description |
|--------|-------------|
| `--game` | Disambiguate mod name |
| `--mod-id` | Nexus mod id |
| `--url` | Nexus mod page URL |

```bash
lmm mod link easysharpening --game kcd2 --url https://www.nexusmods.com/kingdomcomedeliverance2/mods/68
lmm mod link easysharpening --game kcd2 --mod-id 68
```

### `lmm list [game]`

List registered mods. Optional positional `game` or `--game` filters by profile.

Columns: game, name, installed version, latest Nexus version, update flag (`UPDATE` / `—`), enabled, deployed. Use global `--verbose` to include truncated source paths.

---

## Deployment

`enable` / `disable` only change the flag in state. Run `deploy` to apply symlink changes.

### `lmm deploy <game>`

Reconcile the game directory: remove links for disabled mods, symlink enabled mods. Records every link in state for safe `undeploy`.

Honors `--dry-run`. With `--json`, returns link counts, conflicts, and warnings. Exits **1** when conflicts occur (partial deploy).

### `lmm undeploy <game>`

Remove only symlinks recorded in state for this game. Never deletes real game files.

Prompts for confirmation on a TTY unless `--yes` / `-y` is passed. Non-interactive runs require `--yes`.

Honors `--dry-run`. With `--json`, returns link counts and warnings.

### `lmm remove <mod>`

Unregister a mod from state and remove its deployed symlinks. Library files are kept unless `--delete-files` is passed (requires `--yes`).

| Option | Description |
|--------|-------------|
| `--game` | Disambiguate mod name |
| `--yes`, `-y` | Skip confirmation (required in non-interactive mode) |
| `--delete-files` | Delete mod directory under `library_root` (requires `--yes`) |

### `lmm doctor`

Validate config, library paths, deploy targets, mod sources, and deploy layout consistency. Warns when `mod_subdir` games have links deployed flat (run `undeploy` then `deploy` after fixing config). Warns when `mirror` mods have no subdirectories in their source tree. Exits **1** on errors. Use before first deploy to catch setup issues.

### `lmm enable <mod>`

Enable a mod for deployment. Reference by `name` or `game/name`. Use `--game` to disambiguate.

### `lmm disable <mod>`

Disable a mod. Run `deploy` afterward to remove its symlinks.

---

## Nexus (check-only)

Requires a valid Nexus API key. No files are downloaded.

### `lmm identify <game>`

For mods missing `nexus_mod_id`, hash the stored Nexus download file (`download_path`) and search via `md5_search`. If that fails, tries matching the mod name against your Nexus tracked mods list. Fills `nexus_mod_id`, `file_id`, `installed_version`, and `file_md5` in state.

Reports skipped mods (`no_download_file`, `no_nexus_match`) and per-mod API failures. Exits **1** if any mod in the game remains unlinked after the run (use `lmm mod link` for stragglers).

`--dry-run` lists mods that would be identified and their download files locally; no API calls or state writes.

### `lmm check <game>`

Compare installed versions against Nexus latest. Uses `mods/updated` pre-filter and cached responses to stay within rate limits.

Updates `update_available`, `latest_version`, and `last_checked` in state. Reports mods with newer versions available. Notes when version comparison falls back to string equality for non-numeric versions. Exits **1** if any per-mod API failures occurred (other mods still updated in state).

`--dry-run` lists stale mods that would be checked. Live runs also check mods appearing in Nexus `mods/updated` even when `last_checked` is fresh.

---

## Mod references

A mod is referenced as:

- `modname` — unique name within a game, or disambiguated with `--game`
- `game/modname` — fully qualified

## JSON output

`--json` is a **global** pre-command flag (e.g. `lmm --json list kcd2`, not `lmm list --json`).

Commands that support `--json` emit structured objects instead of Rich tables. Examples:

```bash
lmm --json list kcd2
lmm --json deploy kcd2
lmm --json check kcd2
lmm --json doctor
```

`identify` and `check` JSON payloads include `failures` arrays for per-mod errors. `identify` also includes `skips`.
