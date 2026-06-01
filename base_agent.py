"""
Base class for all review agents.
Handles Redis queue polling, result publishing, and metrics.

Subclass and implement `review()`.
"""
from __future__ import annotations
import asyncio, json, logging, os, time
from abc import ABC, abstractmethod
import redis.asyncio as aioredis
from shared.models import ReviewJob, AgentResult, Finding

log = logging.getLogger(__name__)


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self):
        self.redis = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"),
            decode_responses=True
        )
        self.queue_in  = os.getenv("QUEUE_NAME", f"review:{self.name}")
        self.queue_out = "review:results"

    @abstractmethod
    async def review(self, job: ReviewJob) -> list[Finding]:
        """Implement agent logic here. Return findings."""
        ...

    async def run(self):
        log.info(f"[{self.name}] listening on {self.queue_in}")
        while True:
            try:
                # BLPOP blocks until a job arrives (timeout=5 for graceful shutdown)
                raw = await self.redis.blpop(self.queue_in, timeout=5)
                if not raw:
                    continue

                _, payload = raw
                job = ReviewJob.model_validate_json(payload)
                log.info(f"[{self.name}] processing job {job.job_id} — PR #{job.pr_number}")

                t0 = time.monotonic()
                findings = await self.review(job)
                elapsed = int((time.monotonic() - t0) * 1000)

                result = AgentResult(
                    job_id=job.job_id,
                    agent=self.name,
                    findings=findings,
                    duration_ms=elapsed,
                )
                await self.redis.rpush(self.queue_out, result.model_dump_json())
                log.info(f"[{self.name}] done in {elapsed}ms — {len(findings)} findings")

            except Exception as e:
                log.exception(f"[{self.name}] error: {e}")
                await asyncio.sleep(1)

    async def close(self):
        await self.redis.aclose()


def run_agent(agent: BaseAgent):
    import signal
    loop = asyncio.new_event_loop()

    async def _main():
        try:
            await agent.run()
        finally:
            await agent.close()

    def _shutdown(*_):
        loop.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    loop.run_until_complete(_main())
