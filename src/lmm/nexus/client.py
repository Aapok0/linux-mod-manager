"""Nexus Mods v1 API client with retries and disk cache."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx

from lmm import __version__
from lmm.io import atomic_write
from lmm.logging_config import get_logger

logger = get_logger("nexus")

# Per-endpoint cache TTLs (seconds). See nexus-api.md.
CACHE_TTL_DEFAULT = 3600
CACHE_TTL_UPDATED_MODS = 600
CACHE_TTL_MOD_FILES = 900
CACHE_TTL_MD5_SEARCH = 86400


class NexusError(Exception):
    """Raised when Nexus API access fails."""


def _missing_api_key_message() -> str:
    return (
        "Nexus API key missing. Set NEXUS_API_KEY or nexus_api_key in config.toml "
        "(Nexus → account settings → API Access)."
    )


@dataclass
class RateLimitStatus:
    hourly_remaining: int | None = None
    daily_remaining: int | None = None
    hourly_reset: str | None = None
    daily_reset: str | None = None


def _default_cache_path() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_home) if cache_home else Path.home() / ".cache"
    return base / "lmm" / "nexus" / "cache.json"


class NexusClient:
    """Small Nexus API client with retry/backoff and disk cache."""

    def __init__(
        self,
        *,
        api_key: str | None,
        user_agent: str | None = None,
        base_url: str = "https://api.nexusmods.com",
        cache_path: Path | None = None,
        cache_ttl_seconds: int = CACHE_TTL_DEFAULT,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise NexusError(_missing_api_key_message())
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.cache_path = cache_path or _default_cache_path()
        self.cache_ttl_seconds = cache_ttl_seconds
        self.rate_limits = RateLimitStatus()
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_seconds,
            headers={
                "apikey": self.api_key,
                "User-Agent": user_agent or f"lmm/{__version__}",
            },
            transport=transport,
        )
        self._cache: dict[str, dict[str, Any]] = self._load_cache()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> NexusClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        if not self.cache_path.exists():
            return {}
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return raw

    def _save_cache(self) -> None:
        content = json.dumps(self._cache, indent=2) + "\n"
        atomic_write(self.cache_path, content)

    def _cache_key(self, endpoint: str, params: dict[str, Any] | None) -> str:
        payload = {"endpoint": endpoint, "params": params or {}}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()

    def _read_cached(
        self,
        endpoint: str,
        params: dict[str, Any] | None,
    ) -> Any | None:
        key = self._cache_key(endpoint, params)
        entry = self._cache.get(key)
        if not entry:
            return None
        expires_at = float(entry.get("expires_at", 0))
        if expires_at <= time.time():
            self._cache.pop(key, None)
            return None
        logger.debug("Cache hit for %s", endpoint)
        return entry.get("value")

    def _write_cached(
        self,
        endpoint: str,
        params: dict[str, Any] | None,
        value: Any,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        key = self._cache_key(endpoint, params)
        ttl = ttl_seconds if ttl_seconds is not None else self.cache_ttl_seconds
        self._cache[key] = {
            "expires_at": time.time() + max(ttl, 0),
            "value": value,
        }
        self._save_cache()

    def _update_rate_limits(self, response: httpx.Response) -> None:
        headers = response.headers
        hourly = headers.get("x-rl-hourly-remaining")
        daily = headers.get("x-rl-daily-remaining")
        self.rate_limits.hourly_remaining = int(hourly) if hourly else None
        self.rate_limits.daily_remaining = int(daily) if daily else None
        self.rate_limits.hourly_reset = headers.get("x-rl-hourly-reset")
        self.rate_limits.daily_reset = headers.get("x-rl-daily-reset")

    def get_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        use_cache: bool = True,
        ttl_seconds: int | None = None,
        retries: int = 3,
        not_found_ok: bool = False,
    ) -> Any:
        if use_cache:
            cached = self._read_cached(endpoint, params)
            if cached is not None:
                return cached

        for attempt in range(retries):
            try:
                logger.debug("GET %s (attempt %d)", endpoint, attempt + 1)
                response = self._client.get(endpoint, params=params)
            except httpx.HTTPError as exc:
                if attempt == retries - 1:
                    msg = f"Nexus request failed for {endpoint}: {exc}"
                    raise NexusError(msg) from exc
                time.sleep(0.2 * (2**attempt))
                continue

            self._update_rate_limits(response)
            if response.status_code in {401, 403}:
                msg = (
                    "Nexus API key rejected. "
                    "Check NEXUS_API_KEY / config.nexus_api_key."
                )
                raise NexusError(msg)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == retries - 1:
                    msg = (
                        f"Nexus request failed with HTTP {response.status_code}: "
                        f"{endpoint}"
                    )
                    raise NexusError(msg)
                time.sleep(0.3 * (2**attempt))
                continue
            if response.status_code >= 400:
                if not_found_ok and response.status_code == 404:
                    return None
                msg = (
                    f"Nexus request failed with HTTP {response.status_code}: {endpoint}"
                )
                raise NexusError(msg)

            try:
                data = response.json()
            except ValueError as exc:
                msg = f"Nexus response was not valid JSON for {endpoint}"
                raise NexusError(msg) from exc

            if use_cache:
                self._write_cached(endpoint, params, data, ttl_seconds=ttl_seconds)
            return data

        msg = f"Nexus request failed for {endpoint}"
        raise NexusError(msg)

    def validate_key(self) -> dict[str, Any]:
        data = self.get_json("/v1/users/validate.json", use_cache=False)
        if not isinstance(data, dict):
            msg = "Unexpected key validation response from Nexus"
            raise NexusError(msg)
        return data

    def updated_mods(
        self, game_domain: str, *, period: str = "1w"
    ) -> list[dict[str, Any]]:
        data = self.get_json(
            f"/v1/games/{game_domain}/mods/updated.json",
            params={"period": period},
            ttl_seconds=CACHE_TTL_UPDATED_MODS,
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def mod_files(self, game_domain: str, mod_id: int) -> list[dict[str, Any]]:
        data = self.get_json(
            f"/v1/games/{game_domain}/mods/{mod_id}/files.json",
            ttl_seconds=CACHE_TTL_MOD_FILES,
        )
        if isinstance(data, dict):
            files = data.get("files")
            if isinstance(files, list):
                return [item for item in files if isinstance(item, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def md5_search(self, game_domain: str, md5_hash: str) -> list[dict[str, Any]]:
        data = self.get_json(
            f"/v1/games/{game_domain}/mods/md5_search/{md5_hash}.json",
            ttl_seconds=CACHE_TTL_MD5_SEARCH,
            not_found_ok=True,
        )
        if data is None:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            results = data.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
        return []

    def tracked_mods(self) -> list[dict[str, Any]]:
        data = self.get_json("/v1/user/tracked_mods.json")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            tracked = data.get("tracked_mods")
            if isinstance(tracked, list):
                return [item for item in tracked if isinstance(item, dict)]
        return []
