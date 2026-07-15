# linux-mod-manager

Personal CLI mod manager for Linux games and [Nexus Mods](https://www.nexusmods.com/) (`lmm`).

Manage mods in a local library, deploy them into game directories via recorded symlinks, and check Nexus for version updates through API. Downloads require premium membership so not supported yet.

## Install

From a local copy of this repository:

```bash
cd linux-mod-manager
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Shell completion: `lmm --install-completion bash` or `zsh` (see `lmm --show-completion`).

Or install runtime dependencies from the lockfile:

```bash
pip install -r requirements.txt
pip install -e .
```

Development (editable install + test tools):

```bash
pip install -r requirements-dev.txt
```

## Quickstart

### 1. Set environment variables

Get a personal API key from Nexus Mods → account settings → API Access.

```bash
export NEXUS_API_KEY="your-key-here"
```

If you want mods stored somewhere other than the default (`~/.local/share/lmm/mods`), set the library root **before** your first `game add`:

```bash
export LMM_LIBRARY_ROOT="/home/user/Games/lmm/Mods"
```

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NEXUS_API_KEY` | for `identify`/`check` | — | Nexus API key (overrides `nexus_api_key` in config) |
| `LMM_LIBRARY_ROOT` | no | `$XDG_DATA_HOME/lmm/mods` | Mod storage root (overrides `library_root` in config) |

Alternatively, create `~/.config/lmm/config.toml` manually with `library_root` and `nexus_api_key` before running any commands. The first `game add` writes config to disk; any env overrides active at that moment are persisted into `config.toml`. Unsetting `LMM_LIBRARY_ROOT` later does not change the saved path — edit config or set the env var again.

See [Configuration](#configuration) for all options.

### 2. Register a game

Run `lmm doctor` after setup to validate paths and config.

```bash
lmm game add kcd2 \
  --domain kingdomcomedeliverance2 \
  --target "/path/to/KingdomComeDeliverance2/Mods" \
  --library-subpath "KingdomComeDeliverance2/Mods" \
  --deploy-layout mod_subdir
```

`--target` is the default deploy directory (where symlinks land). `--library-subpath` is where mods are stored under your library root. **`--deploy-layout mod_subdir`** is required for KCD2 — each mod must deploy into `Mods/<modname>/`, not flat into `Mods/`. See [Deploy layouts](#deploy-layouts).

### 3. Add a mod

```bash
lmm add /path/to/mod-folder --game kcd2
# or, if the mod already lives in your library:
lmm add mymod --game kcd2
```

Bulk import from a staging directory (one extracted mod per subdirectory):

```bash
# ~/Downloads/kcd2-mods/moda/, modb/, ... — archives at the top level are skipped
lmm add ~/Downloads/kcd2-mods --game kcd2 --all
lmm add ~/Downloads/kcd2-mods --game kcd2 --all --move   # move instead of copy
```

### 4. Deploy

```bash
lmm deploy kcd2
```

Preview without changes: `lmm deploy kcd2 --dry-run`

### 5. Check for Nexus updates

```bash
lmm identify kcd2   # match local files to Nexus mod ids
lmm check kcd2      # report mods with newer versions
```

### 6. List mods

```bash
lmm list kcd2
```

Shows installed version, latest Nexus version, and update status after `check`.

## Common commands

| Command | Purpose |
|---------|---------|
| `lmm game list` | List configured games |
| `lmm list [game]` | List registered mods |
| `lmm enable <mod>` / `lmm disable <mod>` | Toggle deployment (run `deploy` to apply) |
| `lmm undeploy <game>` | Remove all symlinks recorded for a game |
| `lmm game target add/list/remove` | Manage extra deploy directories |

Full command reference: [docs/commands.md](docs/commands.md)

## Deploy layouts

Games expect mods in different on-disk shapes. Set `deploy_layout` on each game profile (via `--deploy-layout` on `game add` or in `config.toml`).

| Layout | Link path | Deploy target should be | Example games |
|--------|-----------|-------------------------|---------------|
| `flat` (default) | `target / file` | Folder mods merge into | Hogwarts Legacy (loose `.pak` in Paks dir) |
| `mod_subdir` | `target / modname / file` | Mod container dir | KCD1, KCD2, Stalker 2 |
| `mirror` | `target / file` (mod tree includes game-relative paths) | Game install root | Oblivion Remastered (multi-path mods) |

### Game layout recipes

**Kingdom Come Deliverance 2** (CryEngine — one folder per mod):

```bash
lmm game add kcd2 \
  --domain kingdomcomedeliverance2 \
  --target "/path/to/KingdomComeDeliverance2/Mods" \
  --library-subpath "KingdomComeDeliverance2/Mods" \
  --deploy-layout mod_subdir
```

**S.T.A.L.K.E.R. 2** (UE — folder per mod under `~mods`):

```bash
lmm game add stalker2 \
  --domain stalker2heartofchornobyl \
  --target "/path/to/Stalker2/Content/Paks/~mods" \
  --library-subpath "Stalker2/Content/Paks/~mods" \
  --deploy-layout mod_subdir
```

**Oblivion Remastered** (multi-path — paks, ESPs, DLLs in one mod tree):

```bash
lmm game add oblivionremastered \
  --domain oblivionremastered \
  --target "/path/to/Oblivion Remastered/OblivionRemastered" \
  --library-subpath "OblivionRemastered/OblivionRemastered" \
  --deploy-layout mirror
```

Import mods with their game-relative directory structure intact (e.g. `Content/Paks/~mods/...`). Use `game target add` for exception mods that need a different root.

**Hogwarts Legacy** (loose pak files):

```bash
lmm game add hogwartslegacy \
  --domain hogwartslegacy \
  --target "/path/to/Hogwarts Legacy/Phoenix/Content/Paks" \
  --deploy-layout flat
```

## Configuration

### File locations

| File | Default path | Override |
|------|--------------|----------|
| Config | `~/.config/lmm/config.toml` | `--config PATH` |
| State | `~/.local/share/lmm/state.json` | `--state PATH` |
| Nexus cache | `~/.cache/lmm/nexus/cache.json` | not configurable |

Defaults follow [XDG Base Directory](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html) when `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, or `XDG_CACHE_HOME` are set.

### `config.toml`

Created on first `game add` or by manual edit. Values below show defaults; env vars override as noted.

```toml
schema_version = 1
# Default when LMM_LIBRARY_ROOT is unset: ~/.local/share/lmm/mods
library_root = "/home/user/.local/share/lmm/mods"
nexus_api_key = ""

[games.kcd2]
nexus_domain = "kingdomcomedeliverance2"
targets = ["/path/to/game/Mods"]
deploy_method = "symlink"
deploy_layout = "mod_subdir"
library_subpath = "KingdomComeDeliverance2/Mods"
```

| Key | Default | Description |
|-----|---------|-------------|
| `schema_version` | `1` | Config format version |
| `library_root` | `$XDG_DATA_HOME/lmm/mods` | Mod storage root; override with `LMM_LIBRARY_ROOT` env |
| `nexus_api_key` | `""` (empty) | API key fallback; prefer `NEXUS_API_KEY` env |
| `games.<id>.nexus_domain` | — (required) | Nexus domain name for API calls |
| `games.<id>.targets` | — (required) | Deploy directories; `[0]` is the default |
| `games.<id>.deploy_method` | `"symlink"` | Deployment method (only `symlink` today) |
| `games.<id>.deploy_layout` | `"flat"` | How mod files map into deploy targets (`flat`, `mod_subdir`, `mirror`) |
| `games.<id>.library_subpath` | `null` | Subpath under `library_root`; defaults to game id |

### `state.json`

Managed by `lmm`; do not edit by hand unless you know the schema. Default path above.

| Key | Default | Description |
|-----|---------|-------------|
| `schema_version` | `1` | State format version |
| `mods` | `[]` | List of mod records |

Per-mod fields (defaults in parentheses): `name`, `game`, `source_path`, `enabled` (true), `target` (null = use `targets[0]`), `nexus_mod_id` (null), `file_id` (null), `installed_version` (null), `file_md5` (null), `deployed_links` ([]), `created_dirs` ([]), `last_checked` (null), `update_available` (false), `latest_version` (null), `notes` (null).

### CLI global options

| Option | Default | Description |
|--------|---------|-------------|
| `--config` | `~/.config/lmm/config.toml` | Config file path |
| `--state` | `~/.local/share/lmm/state.json` | State file path |
| `--json` | off | Machine-readable output |
| `--dry-run` | off | Preview without writes |
| `--verbose`, `-v` | off | Debug logging |

### Environment variables

| Variable | Purpose |
|----------|---------|
| `NEXUS_API_KEY` | Nexus API key (highest priority) |
| `LMM_LIBRARY_ROOT` | Mod library root (overrides `library_root` in config) |
| `XDG_CONFIG_HOME` | Override config base directory |
| `XDG_DATA_HOME` | Override library root and state base directory |
| `XDG_CACHE_HOME` | Override Nexus cache base directory |

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Updating the lockfile

After changing dependencies in `pyproject.toml`:

```bash
pip-compile requirements.in -o requirements.txt --strip-extras
pip-compile requirements-dev.in -o requirements-dev.txt --strip-extras
```

Commit both `.in` (intent) and `.txt` (pinned lock) files.

### Checks

```bash
pytest
ruff format src tests && ruff check src tests
ty check src
```
