"""Minimal async GitHub REST client (PR metadata, changed files, file contents, reviews)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging

import httpx

log = logging.getLogger(__name__)


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Validate GitHub's `X-Hub-Signature-256` HMAC header in constant time."""
    if not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


class GitHubClient:
    def __init__(self, token: str | None, api_url: str = "https://api.github.com"):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(base_url=api_url, headers=headers, timeout=30)

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        for attempt in range(4):
            r = await self._http.request(method, url, **kw)
            # 5xx and secondary rate limits are transient
            if r.status_code >= 500 or (r.status_code in (403, 429) and "rate limit" in r.text.lower()):
                wait = int(r.headers.get("retry-after", 2 ** attempt))
                log.warning("GitHub %s %s → %s; retrying in %ss", method, url, r.status_code, wait)
                await asyncio.sleep(wait)
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    async def get_pr(self, repo: str, number: int) -> dict:
        return (await self._request("GET", f"/repos/{repo}/pulls/{number}")).json()

    async def list_pr_files(self, repo: str, number: int, max_files: int = 300) -> list[dict]:
        files: list[dict] = []
        page = 1
        while len(files) < max_files:
            batch = (
                await self._request("GET", f"/repos/{repo}/pulls/{number}/files", params={"per_page": 100, "page": page})
            ).json()
            files.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return files[:max_files]

    async def get_file(self, repo: str, path: str, ref: str) -> str | None:
        try:
            data = (await self._request("GET", f"/repos/{repo}/contents/{path}", params={"ref": ref})).json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return None

    async def create_review(self, repo: str, number: int, *, commit_id: str, body: str, event: str,
                            comments: list[dict]) -> dict:
        payload = {"commit_id": commit_id, "body": body, "event": event, "comments": comments}
        return (await self._request("POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload)).json()
