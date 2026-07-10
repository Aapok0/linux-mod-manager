"""Tests for logging and API key masking."""

from __future__ import annotations

import logging

from lmm.logging_config import SecretMaskingFilter, mask_secrets, setup_logging


def test_mask_secrets_redacts_api_key_assignment() -> None:
    text = "NEXUS_API_KEY=supersecretkey1234567890"
    assert "***" in mask_secrets(text)
    assert "supersecretkey" not in mask_secrets(text)


def test_mask_secrets_redacts_apikey_header() -> None:
    text = "apikey: abcdefghijklmnopqrstuvwxyz123456"
    masked = mask_secrets(text)
    assert "abcdefghijklmnopqrstuvwxyz" not in masked
    assert "***" in masked


def test_secret_masking_filter_on_log_record() -> None:
    setup_logging(verbose=True)
    record = logging.LogRecord(
        name="lmm.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="key NEXUS_API_KEY=leaked-value-here",
        args=(),
        exc_info=None,
    )
    assert SecretMaskingFilter().filter(record) is True
    assert "leaked-value" not in record.getMessage()
