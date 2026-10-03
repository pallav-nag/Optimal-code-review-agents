"""Dense + sparse embedders.

`FastEmbedEmbedder` runs ONNX models locally (no API key, no GPU): BGE-small for dense
semantics and BM25 sparse vectors for exact identifier matches — code search needs both.
`HashEmbedder` is a deterministic feature-hashing embedder used in tests and the offline demo.
"""
from __future__ import annotations

import asyncio
import math
import re
import zlib
from collections import Counter
from typing import Protocol

SparseVec = tuple[list[int], list[float]]
_TOKEN = re.compile(r"[A-Za-z][a-z0-9]*|[A-Z]+(?![a-z])|\d+")


def code_tokens(text: str) -> list[str]:
    """Identifier-aware tokens: `getUserById` / `get_user_by_id` → get, user, by, id (+ full ident)."""
    out = []
    for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        low = ident.lower()
        out.append(low)
        parts = [p.lower() for p in _TOKEN.findall(ident.replace("_", " "))]
        if len(parts) > 1:
            out.extend(parts)
    return out


class Embedder(Protocol):
    dim: int
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_sparse(self, texts: list[str]) -> list[SparseVec]: ...
    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_query_sparse(self, text: str) -> SparseVec: ...


class HashEmbedder:
    def __init__(self, dim: int = 384):
        self.dim = dim

    def _dense(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok, n in Counter(code_tokens(text)).items():
            h = zlib.crc32(tok.encode())
            vec[h % self.dim] += (1.0 if (h >> 16) & 1 else -1.0) * (1 + math.log(n))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _sparse(self, text: str) -> SparseVec:
        counts: dict[int, float] = {}
        for tok, n in Counter(code_tokens(text)).items():
            idx = zlib.crc32(tok.encode()) & 0x7FFFFFFF
            counts[idx] = counts.get(idx, 0.0) + 1 + math.log(n)
        idxs = sorted(counts)
        return idxs, [counts[i] for i in idxs]

    async def embed(self, texts):
        return [self._dense(t) for t in texts]

    async def embed_sparse(self, texts):
        return [self._sparse(t) for t in texts]

    async def embed_query(self, text):
        return self._dense(text)

    async def embed_query_sparse(self, text):
        return self._sparse(text)


class FastEmbedEmbedder:
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", sparse_model: str = "Qdrant/bm25"):
        from fastembed import SparseTextEmbedding, TextEmbedding

        self._dense_model = TextEmbedding(model_name=model)
        self._sparse_model = SparseTextEmbedding(model_name=sparse_model)
        self.dim = len(next(iter(self._dense_model.embed(["probe"]))))

    async def embed(self, texts):
        return await asyncio.to_thread(lambda: [v.tolist() for v in self._dense_model.embed(texts, batch_size=32)])

    async def embed_sparse(self, texts):
        return await asyncio.to_thread(
            lambda: [(s.indices.tolist(), s.values.tolist()) for s in self._sparse_model.embed(texts, batch_size=32)]
        )

    async def embed_query(self, text):
        return await asyncio.to_thread(lambda: next(iter(self._dense_model.query_embed(text))).tolist())

    async def embed_query_sparse(self, text):
        def _q():
            s = next(iter(self._sparse_model.query_embed(text)))
            return s.indices.tolist(), s.values.tolist()
        return await asyncio.to_thread(_q)


def make_embedder(settings) -> Embedder:
    if settings.embedding_backend == "hash":
        return HashEmbedder()
    return FastEmbedEmbedder(settings.embedding_model, settings.sparse_model)
