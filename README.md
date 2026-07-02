# linux-mod-manager

Personal CLI mod manager for games on Linux and mods in Nexus Mods (`lmm`).

## Development

```bash
python -m venv .venv
source .venv/bin/activate

# Install pinned deps from lockfile
pip install -r requirements-dev.txt

# Or sync exactly (removes packages not in lockfile)
pip-sync requirements-dev.txt
```

### Updating the lockfile

After changing dependencies in `pyproject.toml`, recompile:

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
