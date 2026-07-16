"""Shared Nexus client fakes for service and CLI tests."""

from __future__ import annotations

from typing import Any

from lmm.nexus import NexusError


class FakeNexusClient:
    """Configurable fake implementing Nexus workflow methods."""

    def __init__(
        self,
        *,
        api_key: str | None = "secret",
        fail_first_md5: bool = False,
        fail_mod_files_for: set[int] | None = None,
        md5_results: list[dict[str, Any]] | None = None,
        updated_payload: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> None:
        if api_key is not None and not api_key:
            msg = "Nexus API key missing. Set NEXUS_API_KEY or config.nexus_api_key."
            raise NexusError(msg)
        self.updated_payload = updated_payload or [{"mod_id": 42}, {"mod_id": 43}]
        self.fail_first_md5 = fail_first_md5
        self.fail_mod_files_for = fail_mod_files_for or set()
        self.md5_calls = 0
        self.mod_files_calls: list[int] = []
        self._md5_results = md5_results

    def __enter__(self) -> FakeNexusClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def validate_key(self) -> dict[str, str]:
        return {"name": "tester"}

    def md5_search(self, _: str, md5_hash: str) -> list[dict[str, Any]]:
        self.md5_calls += 1
        if self._md5_results is not None:
            return self._md5_results
        if self.fail_first_md5 and self.md5_calls == 1:
            msg = f"md5_search failed for {md5_hash}"
            raise NexusError(msg)
        mod_id = 42 if self.md5_calls == 1 else 43
        return [{"mod_id": mod_id, "file_id": 7, "version": "1.1.0"}]

    def updated_mods(self, _: str, *, period: str = "1w") -> list[dict[str, Any]]:
        return self.updated_payload

    def mod_files(self, _: str, mod_id: int) -> list[dict[str, Any]]:
        self.mod_files_calls.append(mod_id)
        if mod_id in self.fail_mod_files_for:
            msg = f"mod_files failed for {mod_id}"
            raise NexusError(msg)
        version = "1.2.0" if mod_id == 42 else "2.0.0"
        return [
            {
                "file_id": 7,
                "version": version,
                "category_name": "MAIN",
                "uploaded_timestamp": 1000,
            }
        ]

    def tracked_mods(self) -> list[dict[str, Any]]:
        return []


class HappyNexusClient(FakeNexusClient):
    """Always succeeds on md5_search with mod_id 99."""

    def md5_search(self, _: str, __: str) -> list[dict[str, Any]]:
        return [{"mod_id": 99, "file_id": 5, "version": "1.0.0"}]

    def mod_files(self, _: str, __: int) -> list[dict[str, Any]]:
        return [{"file_id": 5, "version": "1.1.0", "category_name": "MAIN"}]


class FailingFirstMd5Client(FakeNexusClient):
    """Fails first md5_search, succeeds on second — for CLI partial-failure tests."""

    def __init__(self, *, api_key: str | None = "secret", **kwargs: Any) -> None:
        super().__init__(api_key=api_key, fail_first_md5=False, **kwargs)
        self._md5_calls = 0

    def md5_search(self, _: str, __: str) -> list[dict[str, Any]]:
        self._md5_calls += 1
        if self._md5_calls == 1:
            msg = "md5_search failed for first mod"
            raise NexusError(msg)
        return [{"mod_id": 99, "file_id": 5, "version": "1.0.0"}]
