"""Qdrant-backed chunk store with hybrid (dense + BM25 sparse) retrieval fused by RRF."""
from __future__ import annotations

import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from graphreview.retrieval.chunker import Chunk
from graphreview.retrieval.embeddings import Embedder, SparseVec

DENSE, SPARSE = "dense", "sparse"


def _point_id(repo: str, c: Chunk) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{repo}:{c.path}:{c.start_line}:{c.end_line}:{c.kind}"))


class VectorStore:
    def __init__(self, url: str | None, collection: str, embedder: Embedder):
        self.client = AsyncQdrantClient(url=url, check_compatibility=False) if url else AsyncQdrantClient(location=":memory:")
        self._embedded = url is None
        self.collection = collection
        self.embedder = embedder
        self._ready = False

    async def ensure(self) -> None:
        if self._ready:
            return
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                self.collection,
                vectors_config={DENSE: qm.VectorParams(size=self.embedder.dim, distance=qm.Distance.COSINE)},
                sparse_vectors_config={SPARSE: qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
            )
            if not self._embedded:  # embedded mode has no payload indexes
                for field in ("repo", "path", "kind"):
                    await self.client.create_payload_index(self.collection, field, qm.PayloadSchemaType.KEYWORD)
        self._ready = True

    @staticmethod
    def _filter(repo: str, kind: str | None = None, path: str | None = None) -> qm.Filter:
        must = [qm.FieldCondition(key="repo", match=qm.MatchValue(value=repo))]
        if kind:
            must.append(qm.FieldCondition(key="kind", match=qm.MatchValue(value=kind)))
        if path:
            must.append(qm.FieldCondition(key="path", match=qm.MatchValue(value=path)))
        return qm.Filter(must=must)

    async def delete(self, repo: str, paths: list[str] | None = None) -> None:
        await self.ensure()
        if paths is None:
            flt = self._filter(repo)
        else:
            flt = qm.Filter(must=[qm.FieldCondition(key="repo", match=qm.MatchValue(value=repo)),
                                  qm.FieldCondition(key="path", match=qm.MatchAny(any=paths))])
        await self.client.delete(self.collection, points_selector=qm.FilterSelector(filter=flt))

    async def upsert(self, repo: str, chunks: list[Chunk], ref: str | None = None, batch: int = 64) -> int:
        await self.ensure()
        for i in range(0, len(chunks), batch):
            part = chunks[i : i + batch]
            texts = [c.embed_text() for c in part]
            dense = await self.embedder.embed(texts)
            sparse = await self.embedder.embed_sparse(texts)
            points = [
                qm.PointStruct(
                    id=_point_id(repo, c),
                    vector={DENSE: d, SPARSE: qm.SparseVector(indices=s[0], values=s[1])},
                    payload={"repo": repo, "path": c.path, "start_line": c.start_line, "end_line": c.end_line,
                             "kind": c.kind, "symbol": c.symbol, "lang": c.lang, "ref": ref, "text": c.text},
                )
                for c, d, s in zip(part, dense, sparse, strict=True)
            ]
            await self.client.upsert(self.collection, points=points)
        return len(chunks)

    async def hybrid_search(self, repo: str, query: str, k: int = 6, kind: str | None = "code",
                            exclude_paths: list[str] | None = None) -> list[dict]:
        await self.ensure()
        dq = await self.embedder.embed_query(query)
        sq: SparseVec = await self.embedder.embed_query_sparse(query)
        flt = self._filter(repo, kind)
        if exclude_paths:
            flt.must_not = [qm.FieldCondition(key="path", match=qm.MatchAny(any=exclude_paths))]
        res = await self.client.query_points(
            self.collection,
            prefetch=[
                qm.Prefetch(query=dq, using=DENSE, limit=k * 4, filter=flt),
                qm.Prefetch(query=qm.SparseVector(indices=sq[0], values=sq[1]), using=SPARSE, limit=k * 4, filter=flt),
            ],
            query=qm.FusionQuery(fusion=qm.Fusion.RRF),
            limit=k,
            with_payload=True,
        )
        return [{**p.payload, "score": round(p.score, 4)} for p in res.points]

    async def chunks_for(self, repo: str, path: str) -> list[dict]:
        await self.ensure()
        out, offset = [], None
        while True:
            points, offset = await self.client.scroll(
                self.collection, scroll_filter=self._filter(repo, None, path), limit=256, offset=offset,
                with_payload=True, with_vectors=False,
            )
            out.extend(p.payload for p in points)
            if offset is None:
                break
        return sorted(out, key=lambda p: p["start_line"])

    async def count(self, repo: str) -> int:
        await self.ensure()
        return (await self.client.count(self.collection, count_filter=self._filter(repo), exact=True)).count
