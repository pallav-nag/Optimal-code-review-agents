from pathlib import Path
from types import SimpleNamespace

import pytest

from graphreview.common.config import Settings
from graphreview.common.models import IndexRequest
from graphreview.common.state import MemoryStore
from graphreview.indexer.service import Indexer
from graphreview.retrieval.embeddings import HashEmbedder
from graphreview.retrieval.retriever import ContextRetriever, GraphRepository
from graphreview.retrieval.vector_store import VectorStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "eval" / "fixtures" / "shopapp"
CASES = ROOT / "eval" / "cases"
REPO = "acme/shop"


@pytest.fixture
def settings() -> Settings:
    return Settings(bus_backend="memory", state_backend="memory", llm_backend="fake", embedding_backend="hash",
                    graphify_enabled=False, qdrant_url=None, aggregation_timeout_s=30, log_json=False,
                    _env_file=None)


@pytest.fixture
async def retriever() -> ContextRetriever:
    store = MemoryStore()
    graphs = GraphRepository(store)
    vectors = VectorStore(None, "test", HashEmbedder())
    await Indexer(graphs, vectors, graphify_enabled=False).index(IndexRequest(repo=REPO, local_path=str(FIXTURE)))
    return ContextRetriever(graphs, vectors)


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, args, id_="toolu_1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


def response(content, stop_reason="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=50,
                                                 cache_creation_input_tokens=0))


class ScriptedLLM:
    """Replays canned responses and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)
