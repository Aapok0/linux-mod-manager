# Contributing

Personal project — small, focused patches welcome. No maintenance guarantee.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Requires Python **3.11+**. CI tests **3.11, 3.12, and 3.13**. Add **3.14** to CI and `pyproject.toml` classifiers only after CPython **3.14.0** is final (not during pre-release).

Optional: install `p7zip` (`7z` on PATH) to extract `.7z` / `.rar`.

## Checks

```bash
pytest
ruff format src tests && ruff check src tests
ty check src
```

After changing dependencies in `pyproject.toml`:

```bash
pip-compile requirements.in -o requirements.txt --strip-extras
pip-compile requirements-dev.in -o requirements-dev.txt --strip-extras
```

## Guidelines

- Read `.cursor/skills/linux-mod-manager/SKILL.md` and `roadmap.md` before larger changes.
- Keep the state schema backward compatible; bump `schema_version` only for breaking changes.
- Prefer temp dirs and mocked Nexus clients in tests — no live API calls in CI.
- Do not commit API keys or personal game paths.

## Versioning

This project follows [Semantic Versioning](https://semver.org/).

| Bump | When | Examples |
|------|------|----------|
| **MAJOR** | Breaking CLI or state contract | Removed command, `schema_version` break without migration |
| **MINOR** | New backward-compatible capability | New command, new flag with safe default, new optional config |
| **PATCH** | Bug or security fix only | Review fixes, Zip Slip, dry-run contract, dangling symlink |
| **No bump** | Internal quality only | Tests-only PRs, CI/CodeQL/Renovate, docs-only, refactors |

Test-only changes do **not** get their own version tag. Mention them under the next user-facing release in `CHANGELOG.md` if useful.

Review fixes on a feature branch roll into that feature's version when it merges — not as separate patch releases per review commit.

## Releasing

1. Move `[Unreleased]` notes in [`CHANGELOG.md`](CHANGELOG.md) into a new `## [X.Y.Z] - date` section.
2. Bump `version` in [`pyproject.toml`](pyproject.toml) in the same commit.
3. For **1.0+**, set `Development Status :: 5 - Production/Stable` when tagging a stable major release; use `4 - Beta` before that.
4. Create an annotated tag on `main`: `git tag -a vX.Y.Z -m "vX.Y.Z"`.
5. Push the tag: `git push origin vX.Y.Z`.

**Tagging convention:**

- Before the first PR workflow (pre-2026-07-10): tag meaningful commits on `main`.
- After PR workflow: tag merge commits on `main` for user-visible releases.
- Do not tag test/CI-only merges.
