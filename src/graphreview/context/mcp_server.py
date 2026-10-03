"""MCP server exposing the GraphRAG tools to Claude Code, Cursor and other MCP clients.

    claude mcp add graphreview -- graphreview mcp --repo acme/shop --context-url http://localhost:8001

The same index the review agents use becomes available interactively in the IDE.
"""
from __future__ import annotations

from graphreview.common.models import ChangedFile
from graphreview.context.client import HttpContextClient


def build_server(repo: str, context_url: str):
    from mcp.server.mcpserver import MCPServer

    client = HttpContextClient(context_url)
    server = MCPServer(
        name="graphreview",
        instructions=(
            f"Code-graph + semantic search over the indexed repository {repo}. Prefer these tools over "
            "reading whole files: get_callers / get_impact for structure, search_code for similar code, "
            "search_docs for team conventions."
        ),
    )

    @server.tool(description="Who calls a symbol (and what it calls), up to `depth` hops in the code graph.")
    async def get_callers(symbol: str, depth: int = 1) -> str:
        return await client.callers(repo, symbol, depth)

    @server.tool(description="Reverse-dependency impact of edits to the given file/line ranges, plus reaching tests.")
    async def get_impact(path: str, start_line: int, end_line: int) -> str:
        patch = f"@@ -{start_line},0 +{start_line},{end_line - start_line + 1} @@\n" + "+\n" * (end_line - start_line + 1)
        return await client.impact(repo, [ChangedFile(path=path, patch=patch)])

    @server.tool(description="Hybrid semantic + keyword search over code chunks.")
    async def search_code(query: str, k: int = 6) -> str:
        return await client.search_code(repo, query, k)

    @server.tool(description="Search repository docs (CONTRIBUTING, ADRs, READMEs) for conventions.")
    async def search_docs(query: str, k: int = 4) -> str:
        return await client.search_docs(repo, query, k)

    @server.tool(description="Read lines [start, end] of a file from the index (max 200 lines).")
    async def read_snippet(path: str, start: int, end: int) -> str:
        return await client.read_snippet(repo, path, start, end)

    @server.tool(description="Tests that transitively exercise a symbol.")
    async def find_tests(symbol: str) -> str:
        return await client.find_tests(repo, symbol)

    return server


def run(repo: str, context_url: str) -> None:
    build_server(repo, context_url).run("stdio")
