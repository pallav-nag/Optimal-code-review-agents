"""GraphRAG retriever: structural context from the code graph + semantic context from Qdrant.

The agent-facing operations (all return compact, budgeted text):

  impact       diff hunks → enclosing symbols (graph seeds) → reverse-dependency closure,
               plus which tests reach the seeds
  callers      who calls a symbol, N hops
  search_code  hybrid dense+BM25 search over code chunks (RRF)
  search_docs  same over repo docs (CONTRIBUTING, ADRs, READMEs) — team conventions
  read_snippet reassemble exact lines from indexed chunks
  find_tests   tests that transitively exercise a symbol
  pack_context non-agentic baseline: one-shot packed context (graph + code of impacted symbols)
"""
from __future__ import annotations

import gzip
import json
import time
from collections import OrderedDict

from graphreview.common.models import ChangedFile
from graphreview.common.state import StateStore
from graphreview.common.telemetry import RETRIEVAL_SECONDS
from graphreview.retrieval.graph import CodeGraph
from graphreview.retrieval.vector_store import VectorStore


class GraphRepository:
    """Graph blobs live in the state store (gzip JSON) so context replicas stay stateless."""

    def __init__(self, store: StateStore, cache_size: int = 16):
        self.store = store
        self._cache: OrderedDict[str, tuple[str, CodeGraph]] = OrderedDict()
        self._cache_size = cache_size

    async def put(self, repo: str, data: dict, version: str | None = None) -> None:
        version = version or str(time.time())
        await self.store.set(f"graph:{repo}", gzip.compress(json.dumps(data).encode()))
        await self.store.set(f"graph:{repo}:version", version)

    async def get(self, repo: str) -> CodeGraph | None:
        version = await self.store.get(f"graph:{repo}:version")
        if version is None:
            return None
        v = version.decode()
        if (hit := self._cache.get(repo)) and hit[0] == v:
            self._cache.move_to_end(repo)
            return hit[1]
        blob = await self.store.get(f"graph:{repo}")
        if blob is None:
            return None
        graph = CodeGraph(json.loads(gzip.decompress(blob)))
        self._cache[repo] = (v, graph)
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return graph


class _timed:
    def __init__(self, op: str):
        self.op = op

    def __enter__(self):
        self.t0 = time.perf_counter()

    def __exit__(self, *exc):
        RETRIEVAL_SECONDS.labels(self.op).observe(time.perf_counter() - self.t0)


def _numbered(text: str, start: int) -> str:
    return "\n".join(f"{start + i:>5}  {ln}" for i, ln in enumerate(text.splitlines()))


class ContextRetriever:
    def __init__(self, graphs: GraphRepository, vectors: VectorStore):
        self.graphs = graphs
        self.vectors = vectors

    async def _graph(self, repo: str) -> CodeGraph:
        g = await self.graphs.get(repo)
        if g is None:
            raise LookupError(f"repo {repo!r} is not indexed — run the indexer first")
        return g

    # ── structural ───────────────────────────────────────────────────────────
    async def impact(self, repo: str, files: list[ChangedFile], depth: int = 2, budget_chars: int = 6000) -> str:
        with _timed("impact"):
            g = await self._graph(repo)
            seeds: list[str] = []
            lines = ["CHANGED SYMBOLS (graph nodes enclosing the edited lines):"]
            for f in files:
                touched = f.added_lines or [1]
                syms = g.symbols_at(f.path, touched)
                seeds += syms
                if syms:
                    lines += [f"  {g.describe(s)}" for s in syms]
                else:
                    lines.append(f"  {f.path}: not in graph (new file or not indexed)")
            hits = [h for h in g.impact(seeds, depth=depth) if h.node not in seeds]
            lines.append(g.render_hits(f"DEPENDENTS (reverse deps, ≤{depth} hops) — code that may break:", hits,
                                       budget_chars=budget_chars // 2))
            tests = g.tests_for(seeds)
            lines.append("TESTS REACHING THE CHANGE:")
            lines += [f"  {g.describe(t)}" for t in tests[:15]] or ["  (none — changed code appears untested)"]
            return "\n".join(lines)[:budget_chars]

    async def callers(self, repo: str, symbol: str, depth: int = 1) -> str:
        with _timed("callers"):
            g = await self._graph(repo)
            nodes = g.find(symbol)
            if not nodes:
                return f"No symbol named {symbol!r} in the graph."
            parts = []
            for n in nodes:
                parts.append(g.render_hits(f"Callers of {g.describe(n)}:", g.callers(n, depth=depth), 2500))
                parts.append(g.render_hits(f"Callees of {g.describe(n)}:", g.callees(n, depth=1), 1200))
            return "\n".join(parts)

    async def find_tests(self, repo: str, symbol: str) -> str:
        with _timed("find_tests"):
            g = await self._graph(repo)
            nodes = g.find(symbol)
            if not nodes:
                return f"No symbol named {symbol!r} in the graph."
            tests = g.tests_for(nodes)
            if not tests:
                return f"No tests reach {symbol} within 3 hops."
            return "\n".join(f"  {g.describe(t)}" for t in tests[:20])

    # ── semantic ─────────────────────────────────────────────────────────────
    async def search_code(self, repo: str, query: str, k: int = 6, exclude_paths: list[str] | None = None) -> str:
        with _timed("search_code"):
            hits = await self.vectors.hybrid_search(repo, query, k=k, kind="code", exclude_paths=exclude_paths)
        return self._render_hits(hits, max_lines=40)

    async def search_docs(self, repo: str, query: str, k: int = 4) -> str:
        with _timed("search_docs"):
            hits = await self.vectors.hybrid_search(repo, query, k=k, kind="doc")
        return self._render_hits(hits, max_lines=30) if hits else "No repository docs/guidelines indexed."

    @staticmethod
    def _render_hits(hits: list[dict], max_lines: int) -> str:
        out = []
        for h in hits:
            body = "\n".join(h["text"].splitlines()[:max_lines])
            sym = f" ({h['symbol']})" if h.get("symbol") else ""
            out.append(f"--- {h['path']}:{h['start_line']}-{h['end_line']}{sym} score={h['score']}\n"
                       f"{_numbered(body, h['start_line'])}")
        return "\n".join(out) or "No matches."

    async def read_snippet(self, repo: str, path: str, start: int, end: int) -> str:
        with _timed("read_snippet"):
            end = min(end, start + 200)
            chunks = await self.vectors.chunks_for(repo, path)
            lines: dict[int, str] = {}
            for c in chunks:
                if c["end_line"] < start or c["start_line"] > end:
                    continue
                for i, ln in enumerate(c["text"].splitlines()):
                    lines.setdefault(c["start_line"] + i, ln)
            if not lines:
                return f"{path}:{start}-{end} not found in index."
            lo, hi = max(start, min(lines)), min(end, max(lines))
            return "\n".join(f"{n:>5}  {lines.get(n, '')}".rstrip() for n in range(lo, hi + 1))

    # ── one-shot packing (non-agentic baseline / critic context) ─────────────
    async def pack_context(self, repo: str, files: list[ChangedFile], budget_chars: int = 12_000) -> str:
        with _timed("pack_context"):
            parts = [await self.impact(repo, files, budget_chars=budget_chars // 3)]
            g = await self._graph(repo)
            seeds = [s for f in files for s in g.symbols_at(f.path, f.added_lines or [1])]
            used = len(parts[0])
            changed = {f.path for f in files}
            for h in g.impact(seeds, depth=1, limit=8):
                path, start, end = g.location(h.node)
                if not path or not start or path in changed:
                    continue
                snippet = await self.read_snippet(repo, path, start, min(end or start + 30, start + 40))
                block = f"--- dependent: {g.describe(h.node)}\n{snippet}"
                if used + len(block) > budget_chars:
                    break
                parts.append(block)
                used += len(block)
            return "\n\n".join(parts)
