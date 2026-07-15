"""Shared test helpers."""

from __future__ import annotations

import re

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def plain_cli_output(text: str) -> str:
    """Strip Rich/Click ANSI codes for stable CI assertions."""
    return ANSI_ESCAPE_RE.sub("", text)
