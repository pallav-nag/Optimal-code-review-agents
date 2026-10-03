"""In-process pipeline: every service wired over MemoryBus + MemoryStore + embedded Qdrant.

Same code paths as the distributed deployment (same handlers, same consumer groups), just
no broker. Used by `graphreview review-local`, the eval harness and the end-to-end tests.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from graphreview.agents.service import build_agent, run_agent_service
from graphreview.agents.tools import LocalFileSource
from graphreview.aggregator.service import Aggregator
from graphreview.common.bus import MemoryBus
from graphreview.common.config import Settings
from graphreview.common.llm import LLM, make_llm
from graphreview.common.models import FinalReview, IndexRequest, IndexResult, ReviewJob
from graphreview.common.state import MemoryStore
from graphreview.critic.service import Critic
from graphreview.gateway.app import enqueue
from graphreview.indexer.service import Indexer
from graphreview.publisher.service import Publisher
from graphreview.retrieval.embeddings import Embedder, make_embedder
from graphreview.retrieval.retriever import ContextRetriever, GraphRepository
from graphreview.retrieval.vector_store import VectorStore

log = logging.getLogger(__name__)


class LocalPipeline:
    def __init__(self, settings: Settings, llm: LLM | None = None, embedder: Embedder | None = None):
        self.settings = settings
        self.bus = MemoryBus()
        self.store = MemoryStore()
        self.vectors = VectorStore(settings.qdrant_url, settings.qdrant_collection, embedder or make_embedder(settings))
        self.graphs = GraphRepository(self.store)
        self.retriever = ContextRetriever(self.graphs, self.vectors)
        self.llm = llm or make_llm(settings)
        self.files = LocalFileSource()
        self.indexer = Indexer(self.graphs, self.vectors, graphify_enabled=settings.graphify_enabled,
                               graphify_timeout_s=settings.graphify_timeout_s)
        self.agents = [build_agent(r, settings, self.llm, self.retriever, self.files) for r in settings.expected_agents]
        self.aggregator = Aggregator(self.bus, self.store, settings.aggregation_timeout_s, settings.expected_agents)
        self.critic = Critic(self.bus, self.llm, self.retriever, model=settings.critic_model,
                             min_confidence=settings.critic_min_confidence, max_tokens=settings.llm_max_tokens)
        self.publisher = Publisher(self.bus, self.store, gh=None)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.stop()

    async def start(self) -> None:
        await self.bus.start()
        coros = [run_agent_service(self.bus, a, self.settings, self._stop) for a in self.agents]
        coros += [self.aggregator.run(stop=self._stop), self.critic.run(stop=self._stop),
                  self.publisher.run(stop=self._stop)]
        self._tasks = [asyncio.create_task(c) for c in coros]

    async def stop(self) -> None:
        self._stop.set()
        await self.bus.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def index(self, repo: str, path: str | Path) -> IndexResult:
        return await self.indexer.index(IndexRequest(repo=repo, local_path=str(path)))

    async def review(self, job: ReviewJob, timeout_s: float = 900) -> FinalReview:
        await enqueue(self.bus, self.store, self.settings, job)
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if (await self.store.get(f"status:{job.job_id}") or b"") == b"completed":
                return FinalReview.model_validate_json(await self.store.get(f"review:{job.job_id}"))
            for t in self._tasks:
                if t.done() and not t.cancelled() and t.exception():
                    raise t.exception()
            await asyncio.sleep(0.05)
        raise TimeoutError(f"review {job.job_id} did not complete in {timeout_s}s")
