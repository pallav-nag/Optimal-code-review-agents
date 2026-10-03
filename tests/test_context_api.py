import httpx
import pytest
from conftest import REPO

from graphreview.common.models import ChangedFile
from graphreview.context.api import create_app
from graphreview.context.client import HttpContextClient

PATCH = "@@ -19,3 +19,3 @@\n def compute_tax(amount_cents: int, region: str) -> int:\n-    a\n+    b\n     c"


@pytest.fixture
async def client(retriever):
    app = create_app(retriever)
    async with app.router.lifespan_context(app):
        c = HttpContextClient("http://context", transport=httpx.ASGITransport(app=app))
        yield c, retriever
        await c.close()


async def test_http_client_matches_in_process_retriever(client):
    http, local = client
    files = [ChangedFile(path="shopapp/pricing.py", patch=PATCH)]
    assert await http.impact(REPO, files) == await local.impact(REPO, files)
    assert await http.callers(REPO, "order_total", 1) == await local.callers(REPO, "order_total", 1)
    assert await http.search_docs(REPO, "passwords") == await local.search_docs(REPO, "passwords")
    assert await http.read_snippet(REPO, "shopapp/utils.py", 1, 5) == await local.read_snippet(
        REPO, "shopapp/utils.py", 1, 5)


async def test_unknown_repo_maps_to_lookup_error(client):
    http, _ = client
    with pytest.raises(LookupError):
        await http.callers("ghost/repo", "x")
