"""Opt-in: run the real `graphify extract` on the fixture and check CodeGraph reads its output.

    GRAPHIFY_IT=1 .venv/bin/pytest tests/test_graphify_integration.py -v
"""
import asyncio
import os
import shutil

import pytest
from conftest import FIXTURE

from graphreview.indexer.service import Indexer
from graphreview.retrieval.graph import CodeGraph

pytestmark = pytest.mark.skipif(
    os.getenv("GRAPHIFY_IT") != "1" or not shutil.which("graphify"),
    reason="set GRAPHIFY_IT=1 with graphify installed",
)


def test_graphify_graph_supports_impact_queries(tmp_path):
    repo = tmp_path / "shopapp"
    shutil.copytree(FIXTURE, repo)
    indexer = Indexer(graphs=None, vectors=None, graphify_enabled=True)
    data, source = asyncio.run(indexer.build_graph(repo))
    assert source == "graphify", "graphify failed — see the warning log; the indexer fell back to the AST builder"

    g = CodeGraph(data)
    print(g.stats())
    seeds = g.symbols_at("shopapp/pricing.py", [21])
    assert seeds, "no graph symbol encloses pricing.py:21 (source_file/source_location mismatch?)"
    dependents = {g.G.nodes[h.node].get("label", "").rstrip("()") for h in g.impact(seeds, depth=2)}
    assert "order_total" in dependents, f"expected order_total among dependents, got {sorted(dependents)[:20]}"
