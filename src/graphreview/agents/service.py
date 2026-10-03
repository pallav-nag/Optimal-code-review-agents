"""Agent worker: one deployment per role, each role its own Kafka consumer group."""
from __future__ import annotations

import logging
import time

from graphreview.agents.llm_agent import LLMAgent
from graphreview.agents.specs import SPECS
from graphreview.agents.static_agent import StaticAnalysisAgent
from graphreview.agents.tools import FileSource
from graphreview.common.bus import Envelope, EventBus, Topics
from graphreview.common.llm import LLM
from graphreview.common.models import AgentResult, ReviewJob
from graphreview.common.worker import run_consumer
from graphreview.context.client import ContextClient

log = logging.getLogger(__name__)


def build_agent(role: str, settings, llm: LLM, ctx: ContextClient, files: FileSource):
    if role == "static":
        return StaticAnalysisAgent(files)
    if role not in SPECS:
        raise ValueError(f"unknown agent role {role!r}; choose from static, {', '.join(SPECS)}")
    return LLMAgent(SPECS[role], llm, ctx, files, model=settings.agent_model,
                    max_turns=settings.max_agent_turns, max_tokens=settings.llm_max_tokens)


async def run_agent_once(agent, job: ReviewJob) -> AgentResult:
    """Never raises: a failed agent still reports, so the aggregator isn't left waiting."""
    t0 = time.monotonic()
    try:
        return await agent.review(job)
    except Exception as e:
        log.exception("agent %s failed on job %s", agent.name, job.job_id)
        return AgentResult(job_id=job.job_id, agent=agent.name, error=repr(e)[:500],
                           duration_ms=int((time.monotonic() - t0) * 1000))


async def run_agent_service(bus: EventBus, agent, settings, stop=None) -> None:
    async def handle(env: Envelope) -> None:
        job = ReviewJob.model_validate_json(env.value)
        if job.expected_agents and agent.name not in job.expected_agents:
            return
        result = await run_agent_once(agent, job)
        log.info("agent %s: %d findings in %dms%s", agent.name, len(result.findings), result.duration_ms,
                 f" (error: {result.error})" if result.error else "")
        await bus.publish(Topics.AGENT_RESULTS, result, key=job.job_id)

    await run_consumer(bus, service=f"agent-{agent.name}", topics=[Topics.REVIEW_REQUESTED],
                       group=f"agent-{agent.name}", handler=handle,
                       max_attempts=settings.handler_max_attempts, stop=stop)
