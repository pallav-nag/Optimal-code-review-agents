"""
Thin async client for the Graphify MCP server.
Agents use this instead of loading files directly.

Usage:
    async with GraphifyClient(repo="owner/repo") as g:
        # what calls the changed function?
        callers = await g.query("what calls process_payment?")

        # shortest path between two symbols
        path = await g.path("UserService", "DatabasePool")

        # neighbours of a node (immediate dependencies)
        deps = await g.neighbors("AuthMiddleware", depth=2)
"""
from __future__ import annotations
import httpx, os
from typing import Any


GRAPHIFY_MCP_URL = os.getenv("GRAPHIFY_MCP_URL", "http://graphify-mcp:8765")


class GraphifyClient:
    def __init__(self, repo: str):
        # repo = "owner/repo" — MCP server resolves to /repos/owner/repo/graphify-out/graph.json
        self.repo = repo
        self._client: httpx.AsyncClient | None = None
        self._queries = 0

    async def __aenter__(self):
        self._client = httpx.AsyncClient(base_url=GRAPHIFY_MCP_URL, timeout=30)
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    @property
    def query_count(self) -> int:
        return self._queries

    async def _call(self, tool: str, params: dict[str, Any]) -> Any:
        self._queries += 1
        r = await self._client.post("/mcp/tool", json={
            "tool": tool,
            "repo": self.repo,
            **params
        })
        r.raise_for_status()
        return r.json()["result"]

    async def query(self, question: str, budget: int = 2000) -> str:
        """
        Natural-language graph query.
        Returns a compact text summary of the relevant subgraph.
        Typical token cost: 200-800 tokens vs 5k-40k for raw files.
        """
        return await self._call("query_graph", {"query": question, "budget": budget})

    async def path(self, source: str, target: str) -> list[str]:
        """Shortest path between two symbols in the graph."""
        return await self._call("shortest_path", {"source": source, "target": target})

    async def neighbors(self, node: str, depth: int = 1) -> dict[str, Any]:
        """Immediate neighbors (callers + callees) of a symbol."""
        return await self._call("get_neighbors", {"node": node, "depth": depth})

    async def explain(self, symbol: str) -> str:
        """Get Graphify's extracted summary of a symbol."""
        return await self._call("explain_node", {"node": symbol})

    async def impact(self, changed_files: list[str]) -> str:
        """
        Given a list of changed files, return symbols likely affected.
        Useful for test suggester to find what needs testing.
        """
        return await self._call("query_graph", {
            "query": f"what is affected by changes in: {', '.join(changed_files)}",
            "budget": 3000
        })
