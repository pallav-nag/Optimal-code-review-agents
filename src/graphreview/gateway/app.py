"""API gateway: GitHub webhooks + REST API.

POST /webhook                 pull_request → review job; push to default branch → reindex
POST /api/reviews             {repo, pr_number}: review an existing PR on demand
POST /api/reviews/diff        {repo, diff, title}: review a raw diff (repo must be indexed)
GET  /api/reviews/{job_id}    status / final review
POST /api/repos/index         {repo, clone_url?}: (re)index a repository
"""
from __future__ import annotations

import json
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel

from graphreview.common.bus import EventBus, Topics, make_bus
from graphreview.common.config import Settings, get_settings
from graphreview.common.diff import split_git_diff
from graphreview.common.github import GitHubClient, verify_signature
from graphreview.common.models import ChangedFile, FinalReview, IndexRequest, ReviewJob
from graphreview.common.state import StateStore, make_store
from graphreview.context.client import ContextClient, HttpContextClient

log = logging.getLogger(__name__)
TTL = 30 * 24 * 3600
PR_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}
DEMO_REPO = "eval/shopapp"
MAX_HEAD_BYTES = 4 * 1024 * 1024  # keeps the job under Kafka's 8 MB message limit


class ReviewPRReq(BaseModel):
    repo: str
    pr_number: int


class ReviewDiffReq(BaseModel):
    repo: str
    diff: str
    title: str = "ad-hoc review"
    body: str = ""
    head_files: dict[str, str] = {}  # optional full PR-head contents of changed files (enables static analysis)


class IndexReq(BaseModel):
    repo: str
    clone_url: str | None = None
    ref: str | None = None


async def enqueue(bus: EventBus, store: StateStore, settings: Settings, job: ReviewJob) -> dict:
    job.expected_agents = job.expected_agents or list(settings.expected_agents)
    # webhook redeliveries / double "synchronize" events for the same head → one review
    dedupe_key = f"dedupe:{job.repo}:{job.pr_number}:{job.head_sha}" if job.head_sha else None
    if dedupe_key and not await store.set_nx(dedupe_key, job.job_id, ttl_s=TTL):
        existing = (await store.get(dedupe_key) or b"").decode()
        return {"status": "duplicate", "job_id": existing}
    await store.set(f"job:{job.job_id}", job.model_dump_json(), ttl_s=TTL)
    await store.set(f"status:{job.job_id}", "queued", ttl_s=TTL)
    await store.zadd("jobs:recent", job.job_id, job.created_at)
    await bus.publish(Topics.REVIEW_REQUESTED, job, key=job.job_id)
    log.info("enqueued review %s for %s#%s (%d files)", job.job_id, job.repo, job.pr_number, len(job.files))
    return {"status": "enqueued", "job_id": job.job_id}


async def job_from_pr(gh: GitHubClient, repo: str, number: int, pr: dict | None = None) -> ReviewJob:
    pr = pr or await gh.get_pr(repo, number)
    files = await gh.list_pr_files(repo, number)
    return ReviewJob(
        source="github", repo=repo, pr_number=number, title=pr.get("title", ""), body=pr.get("body") or "",
        author=pr.get("user", {}).get("login", ""), base_sha=pr["base"]["sha"], head_sha=pr["head"]["sha"],
        files=[ChangedFile(path=f["filename"], status=f.get("status", "modified"), additions=f.get("additions", 0),
                           deletions=f.get("deletions", 0), patch=f.get("patch", "")) for f in files],
    )


def create_app(bus: EventBus | None = None, store: StateStore | None = None, gh: GitHubClient | None = None,
               settings: Settings | None = None, ctx: ContextClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    st: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st["bus"] = bus or make_bus(settings, "gateway")
        st["store"] = store or make_store(settings)
        st["gh"] = gh or GitHubClient(settings.github_token, settings.github_api_url)
        st["ctx"] = ctx or HttpContextClient(settings.context_service_url)
        await st["bus"].start()
        yield
        await st["bus"].stop()
        await st["store"].close()
        await st["gh"].close()
        if ctx is None:
            await st["ctx"].close()

    app = FastAPI(title="graphreview-gateway", lifespan=lifespan)
    app.mount("/metrics", make_asgi_app())

    @app.post("/webhook")
    async def webhook(request: Request, x_github_event: str = Header(default=""),
                      x_hub_signature_256: str | None = Header(default=None)):
        body = await request.body()
        if settings.github_webhook_secret:
            if not verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
                raise HTTPException(401, "invalid signature")
        elif not settings.allow_unsigned_webhooks:
            raise HTTPException(503, "GITHUB_WEBHOOK_SECRET not configured")
        payload = json.loads(body)
        repo = payload.get("repository", {}).get("full_name", "")

        if x_github_event == "pull_request":
            if payload.get("action") not in PR_ACTIONS or payload["pull_request"].get("draft"):
                return {"status": "ignored", "action": payload.get("action")}
            pr = payload["pull_request"]
            job = await job_from_pr(st["gh"], repo, pr["number"], pr)
            return await enqueue(st["bus"], st["store"], settings, job)

        if x_github_event == "push":
            default = payload.get("repository", {}).get("default_branch", "main")
            if payload.get("ref") != f"refs/heads/{default}":
                return {"status": "ignored", "ref": payload.get("ref")}
            changed = sorted({p for c in payload.get("commits", []) for k in ("added", "modified", "removed")
                              for p in c.get(k, [])})
            req = IndexRequest(repo=repo, clone_url=payload["repository"].get("clone_url"),
                               ref=payload.get("after"), changed_paths=changed or None)
            await st["bus"].publish(Topics.INDEX_REQUESTED, req, key=repo)
            return {"status": "index_requested", "paths": len(changed)}

        return {"status": "ignored", "event": x_github_event}

    @app.post("/api/reviews")
    async def review_pr(req: ReviewPRReq):
        job = await job_from_pr(st["gh"], req.repo, req.pr_number)
        return await enqueue(st["bus"], st["store"], settings, job)

    @app.post("/api/reviews/diff")
    async def review_diff(req: ReviewDiffReq):
        files = [ChangedFile(path=p.path, status=p.status, additions=p.additions, deletions=p.deletions, patch=p.patch)
                 for p in split_git_diff(req.diff)]
        if not files:
            raise HTTPException(422, "no file patches found in diff")
        changed = {f.path for f in files}
        head_files = {p: c for p, c in req.head_files.items() if p in changed}
        if sum(map(len, head_files.values())) > MAX_HEAD_BYTES:
            raise HTTPException(413, "head_files too large")
        job = ReviewJob(source="local", repo=req.repo, title=req.title, body=req.body, files=files,
                        head_files=head_files)
        return await enqueue(st["bus"], st["store"], settings, job)

    async def job_summary(job_id: str) -> dict | None:
        store_ = st["store"]
        status = await store_.get(f"status:{job_id}")
        raw_job = await store_.get(f"job:{job_id}")
        if status is None or raw_job is None:
            return None
        job = ReviewJob.model_validate_json(raw_job)
        out = {"job_id": job_id, "status": status.decode(), "repo": job.repo, "pr_number": job.pr_number,
               "title": job.title, "source": job.source, "created_at": job.created_at,
               "files": [f.path for f in job.files], "expected_agents": job.expected_agents}
        if raw := await store_.get(f"review:{job_id}"):
            review = FinalReview.model_validate_json(raw)
            out.update(verdict=review.verdict.value, completed_at=review.completed_at,
                       severities={sev: sum(1 for f in review.findings if f.severity.value == sev)
                                   for sev in ("critical", "warning", "info", "nitpick")})
        return out

    @app.get("/api/reviews")
    async def list_reviews(limit: int = 20):
        ids = await st["store"].zlatest("jobs:recent", min(max(limit, 1), 100))
        return {"reviews": [s for jid in ids if (s := await job_summary(jid))]}

    @app.get("/api/reviews/{job_id}")
    async def get_review(job_id: str):
        out = await job_summary(job_id)
        if out is None:
            raise HTTPException(404, "unknown job")
        progress = await st["store"].hgetall(f"progress:{job_id}")
        out["progress"] = {agent: json.loads(v) for agent, v in progress.items()}
        job = ReviewJob.model_validate_json(await st["store"].get(f"job:{job_id}"))
        out["patches"] = [{"path": f.path, "status": f.status, "patch": f.patch} for f in job.files]
        if raw := await st["store"].get(f"review:{job_id}"):
            out["review"] = FinalReview.model_validate_json(raw).model_dump(mode="json")
        return out

    @app.get("/api/reviews/{job_id}/impact")
    async def review_impact(job_id: str):
        """The graph impact report the agents were given (proxied from the context service)."""
        raw = await st["store"].get(f"job:{job_id}")
        if raw is None:
            raise HTTPException(404, "unknown job")
        job = ReviewJob.model_validate_json(raw)
        try:
            return {"impact": await st["ctx"].impact(job.repo, job.files)}
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:
            raise HTTPException(503, f"context service unavailable: {e!r}") from e

    @app.get("/api/repos/{owner}/{name}")
    async def repo_status(owner: str, name: str):
        raw = await st["store"].get(f"indexinfo:{owner}/{name}")
        if raw is None:
            raise HTTPException(404, "not indexed")
        return json.loads(raw)

    # ── demo mode: drive the pipeline from the browser with the bundled sample repo ──
    def demo_cases() -> dict[str, dict]:
        cases_dir = Path(settings.demo_dir) / "cases"
        return {c["id"]: c for c in (json.loads(p.read_text()) for p in sorted(cases_dir.glob("*.json")))}

    def require_demo():
        if not settings.demo_mode:
            raise HTTPException(404, "demo mode disabled (DEMO_MODE=true)")

    @app.get("/api/demo/cases")
    async def list_demo_cases():
        require_demo()
        return {"repo": DEMO_REPO, "cases": [{"id": c["id"], "title": c["title"], "body": c.get("body", ""),
                                               "seeded_issues": len(c.get("expected", []))}
                                              for c in demo_cases().values()]}

    @app.post("/api/demo/index")
    async def demo_index():
        require_demo()
        fixture = str((Path(settings.demo_dir) / "fixtures" / "shopapp").resolve())
        await st["bus"].publish(Topics.INDEX_REQUESTED, IndexRequest(repo=DEMO_REPO, local_path=fixture), key=DEMO_REPO)
        return {"status": "index_requested", "repo": DEMO_REPO}

    @app.post("/api/demo/cases/{case_id}")
    async def run_demo_case(case_id: str):
        require_demo()
        from graphreview.eval.cases import materialize

        case = demo_cases().get(case_id)
        if case is None:
            raise HTTPException(404, "unknown case")
        with tempfile.TemporaryDirectory() as tmp:
            head, files, _ = materialize(case, Path(settings.demo_dir) / "fixtures" / "shopapp", Path(tmp))
            head_files = {f.path: (head / f.path).read_text() for f in files}
        job = ReviewJob(source="local", repo=DEMO_REPO, title=case["title"], body=case.get("body", ""), files=files,
                        head_files=head_files)
        return await enqueue(st["bus"], st["store"], settings, job)

    @app.get("/", include_in_schema=False)
    async def ui():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.post("/api/repos/index")
    async def index_repo(req: IndexReq):
        clone = req.clone_url or f"https://github.com/{req.repo}.git"
        await st["bus"].publish(Topics.INDEX_REQUESTED, IndexRequest(repo=req.repo, clone_url=clone, ref=req.ref),
                                key=req.repo)
        return {"status": "index_requested", "repo": req.repo}

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        if not await st["store"].ping():
            raise HTTPException(503, "state store unavailable")
        return {"status": "ready"}

    return app
