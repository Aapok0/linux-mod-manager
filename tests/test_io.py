"""Tests for atomic file writes."""

from __future__ import annotations

from pathlib import Path

from lmm.io import atomic_write


def test_atomic_write_persists_content(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "state.json"
    atomic_write(target, '{"ok": true}\n')
    assert target.read_text(encoding="utf-8") == '{"ok": true}\n'
    assert not list(tmp_path.glob("**/*.tmp"))
