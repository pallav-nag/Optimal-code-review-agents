import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from graphreview.common.bus import MemoryBus, Topics
from graphreview.common.config import Settings
from graphreview.common.models import ReviewJob
from graphreview.common.state import MemoryStore
from graphreview.gateway.app import create_app

SECRET = "s3cret"
PR_EVENT = {
    "action": "opened",
    "repository": {"full_name": "acme/shop", "default_branch": "main"},
    "pull_request": {"number": 42, "title": "Add thing", "body": "", "draft": False, "user": {"login": "dev"},
                     "base": {"sha": "b" * 40}, "head": {"sha": "h" * 40}},
}


class FakeGitHub:
    async def get_pr(self, repo, number):
        return PR_EVENT["pull_request"]

    async def list_pr_files(self, repo, number):
        return [{"filename": "a.py", "status": "modified", "additions": 1, "deletions": 0,
                 "patch": "@@ -1,1 +1,2 @@\n x\n+y"}]

    async def close(self):
        pass


@pytest.fixture
def ctx():
    bus, store = MemoryBus(), MemoryStore()
    settings = Settings(github_webhook_secret=SECRET, expected_agents=["static", "reviewer"], _env_file=None)
    with TestClient(create_app(bus, store, FakeGitHub(), settings)) as client:
        yield client, bus, store


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def post(client, event, payload, signature=None):
    body = json.dumps(payload).encode()
    return client.post("/webhook", content=body, headers={"X-GitHub-Event": event,
                                                          "X-Hub-Signature-256": signature or sign(body)})


def test_rejects_bad_signature(ctx):
    client, bus, _ = ctx
    assert post(client, "pull_request", PR_EVENT, signature="sha256=deadbeef").status_code == 401
    assert not bus.messages(Topics.REVIEW_REQUESTED)


def test_unsigned_webhooks_refused_without_secret():
    settings = Settings(github_webhook_secret=None, allow_unsigned_webhooks=False, _env_file=None)
    with TestClient(create_app(MemoryBus(), MemoryStore(), FakeGitHub(), settings)) as client:
        r = client.post("/webhook", content=b"{}", headers={"X-GitHub-Event": "ping"})
        assert r.status_code == 503


def test_pr_opened_enqueues_job_once(ctx):
    client, bus, _ = ctx
    r = post(client, "pull_request", PR_EVENT)
    assert r.json()["status"] == "enqueued"
    msgs = bus.messages(Topics.REVIEW_REQUESTED)
    job = ReviewJob.model_validate_json(msgs[0].value)
    assert msgs[0].key == job.job_id
    assert job.pr_number == 42 and job.head_sha == "h" * 40 and job.files[0].path == "a.py"
    assert job.expected_agents == ["static", "reviewer"]
    # redelivery of the same head SHA is deduplicated
    assert post(client, "pull_request", PR_EVENT).json() == {"status": "duplicate", "job_id": job.job_id}
    assert len(bus.messages(Topics.REVIEW_REQUESTED)) == 1
    assert client.get(f"/api/reviews/{job.job_id}").json()["status"] == "queued"


def test_push_to_default_branch_requests_incremental_index(ctx):
    client, bus, _ = ctx
    payload = {"ref": "refs/heads/main", "after": "c" * 40,
               "repository": {"full_name": "acme/shop", "default_branch": "main", "clone_url": "https://x/y.git"},
               "commits": [{"added": ["n.py"], "modified": ["a.py"], "removed": []}]}
    assert post(client, "push", payload).json()["status"] == "index_requested"
    req = json.loads(bus.messages(Topics.INDEX_REQUESTED)[0].value)
    assert req["changed_paths"] == ["a.py", "n.py"] and req["ref"] == "c" * 40
    payload["ref"] = "refs/heads/feature"
    assert post(client, "push", payload).json()["status"] == "ignored"


def test_review_raw_diff(ctx):
    client, _, _ = ctx
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n x\n+y\n"
    r = client.post("/api/reviews/diff", json={"repo": "acme/shop", "diff": diff})
    assert r.json()["status"] == "enqueued"
    assert client.post("/api/reviews/diff", json={"repo": "acme/shop", "diff": "nothing"}).status_code == 422


def test_demo_endpoints_and_listing():
    from conftest import ROOT

    bus, store = MemoryBus(), MemoryStore()
    settings = Settings(github_webhook_secret=SECRET, demo_mode=True, demo_dir=str(ROOT / "eval"), _env_file=None)
    with TestClient(create_app(bus, store, FakeGitHub(), settings)) as client:
        cases = client.get("/api/demo/cases").json()["cases"]
        assert len(cases) == 11 and cases[0]["id"].startswith("01")
        job_id = client.post(f"/api/demo/cases/{cases[0]['id']}").json()["job_id"]
        job = ReviewJob.model_validate_json(bus.messages(Topics.REVIEW_REQUESTED)[0].value)
        assert job.job_id == job_id and "shopapp/repository.py" in job.head_files
        assert "WHERE email = '{email" in job.head_files["shopapp/repository.py"]
        listed = client.get("/api/reviews").json()["reviews"]
        assert [r["job_id"] for r in listed] == [job_id] and listed[0]["status"] == "queued"
        detail = client.get(f"/api/reviews/{job_id}").json()
        assert detail["patches"][0]["path"] == "shopapp/repository.py" and detail["progress"] == {}
        assert client.post("/api/demo/index").json()["status"] == "index_requested"
        assert json.loads(bus.messages(Topics.INDEX_REQUESTED)[0].value)["local_path"].endswith("fixtures/shopapp")
        assert "GraphReview" in client.get("/").text


def test_demo_endpoints_off_by_default(ctx):
    client, _, _ = ctx
    assert client.get("/api/demo/cases").status_code == 404


def test_diff_review_keeps_only_changed_head_files(ctx):
    client, bus, _ = ctx
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n x\n+y\n"
    client.post("/api/reviews/diff", json={"repo": "acme/shop", "diff": diff,
                                           "head_files": {"a.py": "x\ny\n", "secrets.py": "nope"}})
    job = ReviewJob.model_validate_json(bus.messages(Topics.REVIEW_REQUESTED)[-1].value)
    assert job.head_files == {"a.py": "x\ny\n"}
