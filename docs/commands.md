# Command reference

Full `lmm` CLI reference. For a short walkthrough, see [README](../README.md#quickstart).

## Global options

These apply to every subcommand:

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | `~/.config/lmm/config.toml` | Path to `config.toml` |
| `--state PATH` | `~/.local/share/lmm/state.json` | Path to `state.json` |
| `--json` | off | Emit machine-readable JSON instead of tables |
| `--dry-run` | off | Print planned actions without filesystem, network, or state writes |
| `--verbose`, `-v` | off | Enable debug logging (API keys are masked) |
| `--version` | — | Print version and exit |

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

```bash
lmm game add kcd2 \
  --domain kingdomcomedeliverance2 \
  --target "/path/to/game/Mods" \
  --library-subpath "KingdomComeDeliverance2/Mods"
```

### `lmm game list`

List all configured game profiles (id, Nexus domain, deploy targets, library subpath).

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

### `lmm add <name_or_path> --game <id>`

Import or register a mod directory.

| Argument / option | Required | Description |
|-------------------|----------|-------------|
| `<name_or_path>` | yes | Mod directory path, or bare mod name under the game's library dir |
| `--game` | yes | Game profile id |
| `--name` | no | Mod name (default: directory name) |
| `--mod-id` | no | Pre-set Nexus mod id |
| `--target-index` | no | Deploy to `targets[n]` instead of default |
| `--target-path` | no | Deploy to an absolute path instead of default |
| `--move` | no | Move mod tree into library instead of copying (outside library only) |
| `--all` | no | Import each immediate subdirectory as a separate mod |

Use only one of `--target-index` or `--target-path`. With `--all`, do not pass `--name`, `--mod-id`, or target overrides.

When `--all` is set, each immediate child **directory** becomes a mod (name = directory name). Top-level **files** (archives, readme) are skipped. Already-registered mods are skipped. Exits **1** only when an import fails (skips are reported but non-fatal). Honors `--dry-run` and `--json`.

```bash
lmm add /path/to/mod --game kcd2
lmm add easysharpening --game kcd2   # when already in library
lmm add ~/Downloads/kcd2-batch --game kcd2 --all
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

Validate config, library paths, deploy targets, and mod sources. Exits **1** on errors. Use before first deploy to catch setup issues.

### `lmm enable <mod>`

Enable a mod for deployment. Reference by `name` or `game/name`. Use `--game` to disambiguate.

### `lmm disable <mod>`

Disable a mod. Run `deploy` afterward to remove its symlinks.

---

## Nexus (check-only)

Requires a valid Nexus API key. No files are downloaded.

### `lmm identify <game>`

For mods missing `nexus_mod_id`, hash the primary file and search Nexus via `md5_search`. Fills `nexus_mod_id`, `file_id`, `installed_version`, and `file_md5` in state.

Reports skipped mods (`no_hashable_file`, `no_nexus_match`) and per-mod API failures. Exits **1** if any failures or unmatched mods remain (successful mods are still saved).

`--dry-run` lists mods that would be identified and their primary files locally; no API calls or state writes.

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
