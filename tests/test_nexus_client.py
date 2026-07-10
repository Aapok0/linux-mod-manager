"""Tests for Nexus API client."""

from __future__ import annotations

import httpx
import pytest

from lmm.nexus.client import (
    CACHE_TTL_MD5_SEARCH,
    NexusClient,
    NexusError,
)


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


def test_cache_hit_avoids_second_request(tmp_path) -> None:
    calls = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"ok": True})

    cache_path = tmp_path / "cache.json"
    transport = httpx.MockTransport(handler)
    with NexusClient(
        api_key="secret",
        cache_path=cache_path,
        transport=transport,
    ) as client:
        first = client.get_json("/v1/test.json", ttl_seconds=60)
        second = client.get_json("/v1/test.json", ttl_seconds=60)
    assert first == second == {"ok": True}
    assert calls["count"] == 1


def test_cache_expires_after_ttl(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}
    now = {"t": 1000.0}

    def fake_time() -> float:
        return now["t"]

    monkeypatch.setattr("lmm.nexus.client.time.time", fake_time)

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"n": calls["count"]})

    cache_path = tmp_path / "cache.json"
    with NexusClient(
        api_key="secret",
        cache_path=cache_path,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.get_json("/v1/test.json", ttl_seconds=10) == {"n": 1}
        now["t"] = 1005.0
        assert client.get_json("/v1/test.json", ttl_seconds=10) == {"n": 1}
        now["t"] = 1011.0
        assert client.get_json("/v1/test.json", ttl_seconds=10) == {"n": 2}
    assert calls["count"] == 2


def test_md5_search_uses_long_ttl_constant(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, int | None] = {}
    original = NexusClient._write_cached

    def spy_write(
        self: NexusClient,
        endpoint: str,
        params: dict | None,
        value: object,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        captured["ttl"] = ttl_seconds
        original(self, endpoint, params, value, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(NexusClient, "_write_cached", spy_write)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with NexusClient(
        api_key="secret",
        cache_path=tmp_path / "cache.json",
        transport=httpx.MockTransport(handler),
    ) as client:
        client.md5_search("kcd2", "abc")
    assert captured["ttl"] == CACHE_TTL_MD5_SEARCH
