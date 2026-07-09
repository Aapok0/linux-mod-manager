"""Tests for Nexus API client."""

from __future__ import annotations

import httpx
import pytest

from lmm.nexus.client import NexusClient, NexusError


def test_client_requires_api_key() -> None:
    with pytest.raises(NexusError, match="API key missing"):
        NexusClient(api_key="")


def test_validate_key_success(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == "secret"
        if request.url.path == "/v1/users/validate.json":
            return httpx.Response(200, json={"name": "tester"})
        return httpx.Response(404, json={"error": "missing"})

    transport = httpx.MockTransport(handler)
    with NexusClient(
        api_key="secret",
        cache_path=tmp_path / "cache.json",
        transport=transport,
    ) as client:
        payload = client.validate_key()
    assert payload["name"] == "tester"


def test_get_json_retries_429(tmp_path) -> None:
    calls = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, json={"error": "rate limit"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    with NexusClient(
        api_key="secret",
        cache_path=tmp_path / "cache.json",
        transport=transport,
    ) as client:
        payload = client.get_json("/v1/test.json", use_cache=False)
    assert payload["ok"] is True
    assert calls["count"] == 2


def test_md5_search_reads_results_wrapper(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".json"):
            return httpx.Response(200, json={"results": [{"mod_id": 10}]})
        return httpx.Response(404, json={})

    with NexusClient(
        api_key="secret",
        cache_path=tmp_path / "cache.json",
        transport=httpx.MockTransport(handler),
    ) as client:
        matches = client.md5_search("kcd2", "abc")
    assert matches == [{"mod_id": 10}]
