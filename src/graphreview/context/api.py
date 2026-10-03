"""Context service — the GraphRAG retrieval API that agents call as tools.

Stateless: graphs come from the state store (cached per replica by version), vectors from
Qdrant. Scale horizontally behind a Service; HPA on CPU.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from prometheus_client import make_asgi_app
from pydantic import BaseModel

from graphreview.common.config import get_settings
from graphreview.common.models import ChangedFile
from graphreview.common.state import make_store
from graphreview.retrieval.embeddings import make_embedder
from graphreview.retrieval.retriever import ContextRetriever, GraphRepository
from graphreview.retrieval.vector_store import VectorStore


class ImpactReq(BaseModel):
    repo: str
    files: list[ChangedFile]
    depth: int = 2


class SymbolReq(BaseModel):
    repo: str
    symbol: str
    depth: int = 1


class SearchReq(BaseModel):
    repo: str
    query: str
    k: int = 6
    exclude_paths: list[str] | None = None


class SnippetReq(BaseModel):
    repo: str
    path: str
    start: int
    end: int


class PackReq(BaseModel):
    repo: str
    files: list[ChangedFile]
    budget_chars: int = 12_000


def build_retriever(settings=None) -> tuple[ContextRetriever, object]:
    settings = settings or get_settings()
    store = make_store(settings)
    vectors = VectorStore(settings.qdrant_url, settings.qdrant_collection, make_embedder(settings))
    return ContextRetriever(GraphRepository(store), vectors), store


def create_app(retriever: ContextRetriever | None = None) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if retriever is None:
            state["retriever"], state["store"] = build_retriever()
        else:
            state["retriever"], state["store"] = retriever, retriever.graphs.store
        yield
        await state["store"].close()

    app = FastAPI(title="graphreview-context", lifespan=lifespan)
    app.mount("/metrics", make_asgi_app())

    def r() -> ContextRetriever:
        return state["retriever"]

    async def guard(coro):
        try:
            return {"result": await coro}
        except LookupError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/v1/impact")
    async def impact(req: ImpactReq):
        return await guard(r().impact(req.repo, req.files, req.depth))

    @app.post("/v1/callers")
    async def callers(req: SymbolReq):
        return await guard(r().callers(req.repo, req.symbol, req.depth))

    @app.post("/v1/find_tests")
    async def find_tests(req: SymbolReq):
        return await guard(r().find_tests(req.repo, req.symbol))

    @app.post("/v1/search_code")
    async def search_code(req: SearchReq):
        return await guard(r().search_code(req.repo, req.query, req.k, req.exclude_paths))

    @app.post("/v1/search_docs")
    async def search_docs(req: SearchReq):
        return await guard(r().search_docs(req.repo, req.query, req.k))

    @app.post("/v1/read_snippet")
    async def read_snippet(req: SnippetReq):
        return await guard(r().read_snippet(req.repo, req.path, req.start, req.end))

    @app.post("/v1/pack_context")
    async def pack_context(req: PackReq):
        return await guard(r().pack_context(req.repo, req.files, req.budget_chars))

    @app.get("/v1/repos/{owner}/{name}/stats")
    async def stats(owner: str, name: str):
        repo = f"{owner}/{name}"
        g = await r().graphs.get(repo)
        if g is None:
            raise HTTPException(404, f"{repo} not indexed")
        return {"repo": repo, "graph": g.stats(), "chunks": await r().vectors.count(repo)}

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        if not await state["store"].ping():
            raise HTTPException(503, "state store unavailable")
        return {"status": "ready"}

    return app
