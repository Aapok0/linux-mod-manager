"""Logging setup with API key masking."""

from __future__ import annotations

import logging
import re

_LOGGER_NAME = "lmm"
_MASKED = "***"

# 32-char hex (Nexus keys) and common env assignment patterns.
_KEY_PATTERNS = (
    re.compile(r"(apikey['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9_-]{16,})", re.IGNORECASE),
    re.compile(r"(NEXUS_API_KEY['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9_-]+)", re.IGNORECASE),
    re.compile(r"(nexus_api_key['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9_-]+)", re.IGNORECASE),
)


def mask_secrets(text: str) -> str:
    masked = text
    for pattern in _KEY_PATTERNS:
        masked = pattern.sub(rf"\1{_MASKED}", masked)
    return masked


class SecretMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        masked = mask_secrets(message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


def setup_logging(*, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger(_LOGGER_NAME)
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(SecretMaskingFilter())
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"{_LOGGER_NAME}.{name}")
    return logging.getLogger(_LOGGER_NAME)
