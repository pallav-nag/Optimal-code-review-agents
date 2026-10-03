from conftest import FIXTURE, REPO

from graphreview.common.models import ChangedFile
from graphreview.retrieval.chunker import chunk_file

TAX_PATCH = """@@ -19,3 +19,3 @@
 def compute_tax(amount_cents: int, region: str) -> int:
-    rate_bps = TAX_RATES_BPS.get(region, 0)
+    rate_bps = TAX_RATES_BPS[region]
     return (amount_cents * rate_bps + 5_000) // 10_000"""


def test_python_chunks_tile_the_file():
    text = (FIXTURE / "shopapp" / "payments.py").read_text()
    chunks = chunk_file("shopapp/payments.py", text)
    covered = set()
    for c in chunks:
        covered |= set(range(c.start_line, c.end_line + 1))
    non_blank = {i + 1 for i, ln in enumerate(text.splitlines()) if ln.strip()}
    assert non_blank <= covered
    assert any(c.symbol == "PaymentGateway" for c in chunks)


def test_markdown_chunks_are_docs():
    chunks = chunk_file("CONTRIBUTING.md", (FIXTURE / "CONTRIBUTING.md").read_text())
    assert chunks and all(c.kind == "doc" for c in chunks)
    assert "Money" in {c.symbol for c in chunks}


async def test_impact_report(retriever):
    out = await retriever.impact(REPO, [ChangedFile(path="shopapp/pricing.py", patch=TAX_PATCH)])
    assert "compute_tax" in out.split("DEPENDENTS")[0]
    assert "order_total" in out and "checkout" in out
    assert "test_tax_rounds_half_up" in out


async def test_hybrid_search_finds_conventions_and_code(retriever):
    docs = await retriever.search_docs(REPO, "money must be integer cents not float")
    assert docs.splitlines()[0].startswith("--- CONTRIBUTING.md") and "Money" in docs.splitlines()[0]
    code = await retriever.search_code(REPO, "refund charge payment provider", k=3)
    assert "shopapp/payments.py" in code


async def test_read_snippet_reassembles_lines(retriever):
    out = await retriever.read_snippet(REPO, "shopapp/pricing.py", 19, 21)
    assert out.splitlines()[0].strip().startswith("19  def compute_tax")
    assert len(out.splitlines()) == 3


async def test_unindexed_repo_raises_lookup(retriever):
    import pytest

    with pytest.raises(LookupError):
        await retriever.callers("nobody/nothing", "x")
