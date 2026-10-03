from conftest import FIXTURE

from graphreview.indexer.service import normalize_graph_paths
from graphreview.retrieval.graph import CodeGraph
from graphreview.retrieval.graph_builder import build_python_graph


def graph() -> CodeGraph:
    return CodeGraph(build_python_graph(FIXTURE))


def test_symbols_at_resolves_innermost_symbol():
    g = graph()
    assert g.symbols_at("shopapp/pricing.py", [21]) == ["shopapp/pricing.py::compute_tax"]
    # method inside a class
    assert g.symbols_at("shopapp/payments.py", [26]) == ["shopapp/payments.py::PaymentGateway.charge"]
    # line outside any def falls back to the file node
    assert g.symbols_at("shopapp/pricing.py", [3]) == ["shopapp/pricing.py"]


def test_import_aware_call_resolution():
    g = graph()
    callers = {h.node for h in g.callers("shopapp/pricing.py::order_total")}
    assert "shopapp/api.py::checkout" in callers  # via `from shopapp import pricing; pricing.order_total(...)`
    callee_ids = {h.node for h in g.callees("shopapp/api.py::checkout")}
    assert "shopapp/repository.py::OrderRepository.create" in callee_ids or any("create" in c for c in callee_ids)


def test_impact_is_reverse_transitive_and_finds_tests():
    g = graph()
    seeds = ["shopapp/pricing.py::compute_tax"]
    hits = {h.node: h.distance for h in g.impact(seeds, depth=2)}
    assert hits["shopapp/pricing.py::order_total"] == 1
    assert hits["shopapp/api.py::checkout"] == 2
    tests = g.tests_for(seeds)
    assert "tests/test_pricing.py::test_tax_rounds_half_up" in tests


def test_graphify_style_nodes_and_absolute_paths():
    """Graphify file nodes have label == basename and no location; paths may be absolute."""
    data = {
        "nodes": [
            {"id": "app_py", "label": "app.py", "file_type": "code", "source_file": "/work/repo/app.py",
             "source_location": None},
            {"id": "app_handler", "label": "handler()", "file_type": "code", "source_file": "/work/repo/app.py",
             "source_location": "L5"},
            {"id": "app_helper", "label": "helper()", "file_type": "code", "source_file": "/work/repo/app.py",
             "source_location": "L20"},
        ],
        "links": [{"source": "app_handler", "target": "app_helper", "relation": "calls", "confidence": "EXTRACTED"}],
    }
    from pathlib import Path

    g = CodeGraph(normalize_graph_paths(data, Path("/work/repo")))
    assert g.symbols_at("app.py", [22]) == ["app_helper"]
    assert [h.node for h in g.callers("app_helper")] == ["app_handler"]
    assert g.find("helper") == ["app_helper"]
