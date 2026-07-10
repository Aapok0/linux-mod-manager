# Command reference

Full `lmm` CLI reference. For a short walkthrough, see [README](../README.md#quickstart).

## Global options

These apply to every subcommand:

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | `~/.config/lmm/config.toml` | Path to `config.toml` |
| `--state PATH` | `~/.local/share/lmm/state.json` | Path to `state.json` |
| `--json` | off | Emit machine-readable JSON instead of tables |
| `--dry-run` | off | Print planned actions without writing files or state |
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
| `--library-subpath` | no | Subpath under `library_root` for this game's mods |

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

Use only one of `--target-index` or `--target-path`.

```bash
lmm add /path/to/mod --game kcd2
lmm add easysharpening --game kcd2   # when already in library
```

### `lmm list [game]`

List registered mods. Optional `game` argument or `--game` filters by profile.

Columns: game, name, installed version, latest Nexus version, update flag, enabled, deployed, source path.

---

## Deployment

`enable` / `disable` only change the flag in state. Run `deploy` to apply symlink changes.

### `lmm deploy <game>`

Reconcile the game directory: remove links for disabled mods, symlink enabled mods. Records every link in state for safe `undeploy`.

Honors `--dry-run`. With `--json`, returns link counts, conflicts, and warnings.

### `lmm undeploy <game>`

Remove only symlinks recorded in state for this game. Never deletes real game files.

Honors `--dry-run`. With `--json`, returns link counts and warnings.

### `lmm enable <mod>`

Enable a mod for deployment. Reference by `name` or `game/name`. Use `--game` to disambiguate.

### `lmm disable <mod>`

Disable a mod. Run `deploy` afterward to remove its symlinks.

---

## Nexus (check-only)

Requires a valid Nexus API key. No files are downloaded.

### `lmm identify <game>`

For mods missing `nexus_mod_id`, hash the primary file and search Nexus via `md5_search`. Fills `nexus_mod_id`, `file_id`, `installed_version`, and `file_md5` in state.

Per-mod API failures are reported and skipped; other mods continue.

### `lmm check <game>`

Compare installed versions against Nexus latest. Uses `mods/updated` pre-filter and cached responses to stay within rate limits.

Updates `update_available`, `latest_version`, and `last_checked` in state. Reports mods with newer versions available.

---

## Mod references

A mod is referenced as:

- `modname` — unique name within a game, or disambiguated with `--game`
- `game/modname` — fully qualified

## JSON output

Commands that support `--json` emit structured objects instead of Rich tables. Examples:

```bash
lmm --json list kcd2
lmm --json deploy kcd2
lmm --json check kcd2
```

`identify` and `check` JSON payloads include `failures` arrays for per-mod errors.
