"""Indexer: builds the code graph and the vector index for a repository.

Triggered by `repo.index.requested` (push to the default branch, or manual). Steps:
  1. shallow clone / fetch the ref (token passed as an HTTP header, never stored in the remote URL)
  2. code graph: `graphify extract --code-only` (tree-sitter, 25+ languages, no LLM cost);
     falls back to the built-in Python AST builder if Graphify is unavailable or fails
  3. chunk → embed (dense + BM25) → upsert into Qdrant; on push only the changed paths are re-embedded
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path

from graphreview.common.bus import Envelope, EventBus, Topics
from graphreview.common.models import IndexRequest, IndexResult
from graphreview.common.telemetry import INDEX_SECONDS
from graphreview.common.worker import run_consumer
from graphreview.retrieval.chunker import CODE_EXT, DOC_EXT, Chunk, chunk_file
from graphreview.retrieval.graph_builder import SKIP_DIRS, build_python_graph
from graphreview.retrieval.retriever import GraphRepository
from graphreview.retrieval.vector_store import VectorStore

log = logging.getLogger(__name__)
INDEXABLE = set(CODE_EXT) | set(DOC_EXT)
REPO_RE = re.compile(r"[\w.-]+/[\w.-]+")


async def _run(*cmd: str, cwd: str | None = None, timeout_s: int = 600) -> str:
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed: {err.decode()[-500:]}")
    return out.decode()


def normalize_graph_paths(data: dict, root: Path) -> dict:
    """Make `source_file` repo-relative so graph lookups match diff paths."""
    prefixes = [str(root.resolve()) + "/", str(root) + "/"]

    def rel(p):
        if isinstance(p, str):
            for pre in prefixes:
                if p.startswith(pre):
                    return p[len(pre):]
            return p.removeprefix("./")
        return p

    for n in data.get("nodes", []):
        n["source_file"] = rel(n.get("source_file"))
    for e in data.get("links", data.get("edges", [])):
        e["source_file"] = rel(e.get("source_file"))
    return data


def collect_chunks(root: Path, only: list[str] | None = None) -> list[Chunk]:
    paths = [root / p for p in only] if only is not None else sorted(root.rglob("*"))
    chunks: list[Chunk] = []
    for p in paths:
        if not p.is_file() or p.suffix.lower() not in INDEXABLE:
            continue
        rel = p.relative_to(root)
        if set(rel.parts) & SKIP_DIRS:
            continue
        try:
            chunks += chunk_file(rel.as_posix(), p.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            log.debug("skip %s: %s", rel, e)
    return chunks


class Indexer:
    def __init__(self, graphs: GraphRepository, vectors: VectorStore, *, repos_dir: str = "/tmp/graphreview/repos",  # noqa: S108
                 github_token: str | None = None, graphify_enabled: bool = True, graphify_timeout_s: int = 600):
        self.graphs, self.vectors = graphs, vectors
        self.repos_dir = Path(repos_dir)
        self.token = github_token
        self.graphify_enabled = graphify_enabled
        self.graphify_timeout_s = graphify_timeout_s

    def _git_auth(self) -> list[str]:
        if not self.token:
            return []
        cred = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        return ["-c", f"http.extraHeader=Authorization: Basic {cred}"]

    async def checkout(self, req: IndexRequest) -> Path:
        if not REPO_RE.fullmatch(req.repo) or ".." in req.repo:
            raise ValueError(f"invalid repo name {req.repo!r}")
        dest = self.repos_dir / req.repo
        url = req.clone_url or f"https://github.com/{req.repo}.git"
        ref = req.ref or "HEAD"
        if not (dest / ".git").exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            await _run("git", *self._git_auth(), "clone", "--depth", "1", "--no-tags", url, str(dest))
        if req.ref:
            await _run("git", *self._git_auth(), "fetch", "--depth", "1", "origin", ref, cwd=str(dest))
            await _run("git", "checkout", "--force", "FETCH_HEAD", cwd=str(dest))
        return dest

    async def build_graph(self, root: Path) -> tuple[dict, str]:
        if self.graphify_enabled and shutil.which("graphify"):
            with tempfile.TemporaryDirectory() as out:
                try:
                    await _run("graphify", "extract", str(root), "--code-only", "--out", out,
                               timeout_s=self.graphify_timeout_s)
                    data = json.loads(Path(out, "graphify-out", "graph.json").read_text())
                    return normalize_graph_paths(data, root), "graphify"
                except Exception as e:
                    log.warning("graphify failed (%s); using built-in AST graph", e)
        return await asyncio.to_thread(build_python_graph, root), "builtin"

    async def index(self, req: IndexRequest) -> IndexResult:
        t0 = time.monotonic()
        root = Path(req.local_path) if req.local_path else await self.checkout(req)
        with INDEX_SECONDS.labels("graph").time():
            data, source = await self.build_graph(root)
        await self.graphs.put(req.repo, data, version=req.ref or str(time.time()))

        with INDEX_SECONDS.labels("embed").time():
            if req.changed_paths is not None:
                await self.vectors.delete(req.repo, req.changed_paths)
                existing = [p for p in req.changed_paths if (root / p).is_file()]
                chunks = collect_chunks(root, existing)
            else:
                await self.vectors.delete(req.repo)
                chunks = collect_chunks(root)
            await self.vectors.upsert(req.repo, chunks, ref=req.ref)

        result = IndexResult(repo=req.repo, ref=req.ref, graph_nodes=len(data.get("nodes", [])),
                             graph_edges=len(data.get("links", data.get("edges", []))), graph_source=source,
                             chunks=len(chunks), duration_ms=int((time.monotonic() - t0) * 1000))
        await self.graphs.store.set(f"indexinfo:{req.repo}", result.model_dump_json())
        log.info("indexed %s: %d nodes / %d edges (%s), %d chunks in %dms", req.repo, result.graph_nodes,
                 result.graph_edges, source, result.chunks, result.duration_ms)
        return result

    async def run(self, bus: EventBus, max_attempts: int = 3, stop=None) -> None:
        async def handle(env: Envelope) -> None:
            result = await self.index(IndexRequest.model_validate_json(env.value))
            await bus.publish(Topics.INDEXED, result, key=result.repo)

        await run_consumer(bus, service="indexer", topics=[Topics.INDEX_REQUESTED], group="indexer",
                           handler=handle, max_attempts=max_attempts, stop=stop)
