"""Clients for the context service. `ContextRetriever` itself satisfies the same protocol,
so the in-process pipeline (tests, demo, eval) skips HTTP entirely."""
from __future__ import annotations

from typing import Protocol

import httpx

from graphreview.common.models import ChangedFile


class ContextClient(Protocol):
    async def impact(self, repo: str, files: list[ChangedFile], depth: int = 2) -> str: ...
    async def callers(self, repo: str, symbol: str, depth: int = 1) -> str: ...
    async def find_tests(self, repo: str, symbol: str) -> str: ...
    async def search_code(self, repo: str, query: str, k: int = 6, exclude_paths: list[str] | None = None) -> str: ...
    async def search_docs(self, repo: str, query: str, k: int = 4) -> str: ...
    async def read_snippet(self, repo: str, path: str, start: int, end: int) -> str: ...
    async def pack_context(self, repo: str, files: list[ChangedFile], budget_chars: int = 12_000) -> str: ...


class HttpContextClient:
    def __init__(self, base_url: str, timeout: float = 30, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._http.aclose()

    async def _post(self, op: str, body: dict) -> str:
        r = await self._http.post(f"/v1/{op}", json=body)
        if r.status_code == 404:
            raise LookupError(r.json().get("detail", "not found"))
        r.raise_for_status()
        return r.json()["result"]

    async def impact(self, repo, files, depth=2):
        return await self._post("impact", {"repo": repo, "files": [f.model_dump() for f in files], "depth": depth})

    async def callers(self, repo, symbol, depth=1):
        return await self._post("callers", {"repo": repo, "symbol": symbol, "depth": depth})

    async def find_tests(self, repo, symbol):
        return await self._post("find_tests", {"repo": repo, "symbol": symbol})

    async def search_code(self, repo, query, k=6, exclude_paths=None):
        return await self._post("search_code", {"repo": repo, "query": query, "k": k, "exclude_paths": exclude_paths})

    async def search_docs(self, repo, query, k=4):
        return await self._post("search_docs", {"repo": repo, "query": query, "k": k})

    async def read_snippet(self, repo, path, start, end):
        return await self._post("read_snippet", {"repo": repo, "path": path, "start": start, "end": end})

    async def pack_context(self, repo, files, budget_chars=12_000):
        return await self._post(
            "pack_context", {"repo": repo, "files": [f.model_dump() for f in files], "budget_chars": budget_chars}
        )
