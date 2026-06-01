"""
Aggregator
==========
Waits for all 3 agents to complete for a job_id,
merges + deduplicates findings, pushes to critic queue.

Timeout: if not all agents respond in 60s, proceed with partial results.
"""
from __future__ import annotations
import asyncio, logging, os, time
import redis.asyncio as aioredis
from shared.models import AgentResult, AggregatedResult, Finding

log = logging.getLogger(__name__)

REDIS_URL  = os.getenv("REDIS_URL", "redis://localhost:6379")
QUEUE_IN   = os.getenv("QUEUE_IN",  "review:results")
QUEUE_OUT  = os.getenv("QUEUE_OUT", "review:aggregated")
AGENTS     = {"static_analysis", "llm_reviewer", "test_suggester"}
TIMEOUT_S  = 60


async def deduplicate(findings: list[Finding]) -> list[Finding]:
    """Remove findings where (file, line, message) are near-identical."""
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in sorted(findings, key=lambda x: x.confidence, reverse=True):
        key = (f.file, f.line, f.message[:60])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


async def run():
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    # job_id → {agent_name: AgentResult}
    pending: dict[str, dict[str, AgentResult]] = {}
    # job_id → first_seen timestamp
    first_seen: dict[str, float] = {}

    log.info(f"Aggregator listening on {QUEUE_IN}")

    async def flush(job_id: str, results: dict[str, AgentResult]):
        all_findings = [f for r in results.values() for f in r.findings]
        deduped = await deduplicate(all_findings)
        total_tokens = sum(r.token_usage for r in results.values())

        agg = AggregatedResult(
            job_id=job_id,
            repo_full_name="",   # filled from first result metadata
            pr_number=0,         # same
            findings=deduped,
            total_tokens=total_tokens,
            ready_to_post=True,
        )
        await redis.rpush(QUEUE_OUT, agg.model_dump_json())
        del pending[job_id]
        del first_seen[job_id]
        log.info(f"Flushed job {job_id} — {len(deduped)} findings after dedup")

    while True:
        # Check timeouts
        now = time.time()
        for job_id, ts in list(first_seen.items()):
            if now - ts > TIMEOUT_S:
                log.warning(f"Timeout for {job_id}, flushing partial")
                await flush(job_id, pending[job_id])

        raw = await redis.blpop(QUEUE_IN, timeout=5)
        if not raw:
            continue

        _, payload = raw
        result = AgentResult.model_validate_json(payload)
        jid = result.job_id

        if jid not in pending:
            pending[jid] = {}
            first_seen[jid] = time.time()

        pending[jid][result.agent] = result

        if pending[jid].keys() >= AGENTS:
            await flush(jid, pending[jid])


if __name__ == "__main__":
    asyncio.run(run())
