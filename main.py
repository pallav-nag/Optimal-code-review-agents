"""
API Gateway
===========
Receives GitHub PR webhooks, validates HMAC signature,
fans the job out to all agent queues simultaneously.
"""
from __future__ import annotations
import hashlib, hmac, json, logging, os
import redis.asyncio as aioredis
from fastapi import FastAPI, Header, HTTPException, Request
from shared.models import ReviewJob

log = logging.getLogger(__name__)
app = FastAPI()

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").encode()
REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379")

# All agent input queues — fan out to all simultaneously
AGENT_QUEUES = [
    "review:static",
    "review:llm",
    "review:tests",
]

redis: aioredis.Redis | None = None


@app.on_event("startup")
async def startup():
    global redis
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)


@app.on_event("shutdown")
async def shutdown():
    await redis.aclose()


def _verify_signature(body: bytes, sig_header: str) -> bool:
    if not WEBHOOK_SECRET:
        return True  # dev mode
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header or "")


@app.post("/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
):
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    if x_github_event != "pull_request":
        return {"status": "ignored", "event": x_github_event}

    payload = json.loads(body)
    action  = payload.get("action", "")

    if action not in ("opened", "synchronize", "reopened"):
        return {"status": "ignored", "action": action}

    pr   = payload["pull_request"]
    repo = payload["repository"]["full_name"]

    # Build changed files list from commits API
    # (simplified: real impl would call GitHub API for the file list)
    changed_files = [f["filename"] for f in payload.get("files", [])]

    job = ReviewJob(
        repo_full_name=repo,
        pr_number=pr["number"],
        pr_title=pr["title"],
        base_sha=pr["base"]["sha"],
        head_sha=pr["head"]["sha"],
        changed_files=changed_files,
        diff_patch=pr.get("patch", ""),  # fetched separately in prod
    )

    # Fan out to all agent queues (non-blocking)
    pipe = redis.pipeline()
    for q in AGENT_QUEUES:
        pipe.rpush(q, job.model_dump_json())
    await pipe.execute()

    log.info(f"Enqueued job {job.job_id} to {len(AGENT_QUEUES)} queues")
    return {"status": "enqueued", "job_id": job.job_id}


@app.get("/health")
async def health():
    await redis.ping()
    return {"status": "ok"}
