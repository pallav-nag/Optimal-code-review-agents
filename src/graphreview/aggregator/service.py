"""Aggregator: joins agent results per job, merges duplicate findings, hands off to the critic.

State lives in the state store, not in process memory, so the aggregator survives restarts
and can run as several replicas:
  job:{id}            the ReviewJob (written by the gateway)
  agg:{id}            hash agent → AgentResult JSON (idempotent under redelivery)
  agg:deadlines       zset job_id → deadline; a sweeper flushes partial results on timeout
  agg:done:{id}       SETNX guard so a job is flushed exactly once across replicas
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from difflib import SequenceMatcher

from graphreview.common.bus import Envelope, EventBus, Topics
from graphreview.common.models import AgentResult, AggregatedReview, Finding, ReviewJob, Usage
from graphreview.common.state import StateStore
from graphreview.common.worker import run_consumer

log = logging.getLogger(__name__)
LINE_WINDOW = 3
SIMILARITY = 0.55
DEADLINES = "agg:deadlines"
TTL = 7 * 24 * 3600


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower())


def _similar(a: Finding, b: Finding) -> bool:
    if a.file != b.file:
        return False
    if a.line is not None and b.line is not None and abs(a.line - b.line) > LINE_WINDOW:
        return False
    if a.category == b.category:
        return True
    ta, tb = _norm(a.title + " " + a.message), _norm(b.title + " " + b.message)
    return SequenceMatcher(None, ta, tb).ratio() >= SIMILARITY


def merge_findings(findings: list[Finding]) -> list[Finding]:
    """Cluster near-duplicates (same file, ±3 lines, same category or similar text).

    The representative is the most severe / most confident finding; agreement between
    independent agents raises confidence (a cheap ensemble signal the critic also sees).
    """
    ordered = sorted(findings, key=lambda f: (-f.severity.rank, -f.confidence))
    clusters: list[list[Finding]] = []
    for f in ordered:
        for c in clusters:
            if _similar(c[0], f):
                c.append(f)
                break
        else:
            clusters.append([f])
    out = []
    for c in clusters:
        rep = c[0].model_copy(deep=True)
        agents = sorted({f.agent for f in c})
        rep.reported_by = agents
        if len(agents) > 1:
            rep.confidence = min(1.0, 1 - (1 - rep.confidence) * 0.6 ** (len(agents) - 1))
        for f in c[1:]:
            rep.evidence += [e for e in f.evidence if e not in rep.evidence][:2]
            if not rep.suggestion and f.suggestion:
                rep.suggestion = f.suggestion
        out.append(rep)
    return sorted(out, key=lambda f: (f.file, f.line or 0))


class Aggregator:
    def __init__(self, bus: EventBus, store: StateStore, timeout_s: int = 300, default_agents: list[str] | None = None):
        self.bus, self.store, self.timeout_s = bus, store, timeout_s
        self.default_agents = default_agents or []

    async def _job(self, job_id: str) -> ReviewJob | None:
        raw = await self.store.get(f"job:{job_id}")
        return ReviewJob.model_validate_json(raw) if raw else None

    async def handle(self, env: Envelope) -> None:
        result = AgentResult.model_validate_json(env.value)
        jid = result.job_id
        if await self.store.get(f"agg:done:{jid}"):
            return  # late or duplicate result after flush
        await self.store.hset(f"agg:{jid}", result.agent, result.model_dump_json(), ttl_s=TTL)
        await self.store.hset(f"progress:{jid}", result.agent, json.dumps(result.stats()), ttl_s=TTL)
        await self.store.set(f"status:{jid}", "agents", ttl_s=TTL)
        job = await self._job(jid)
        # deadline anchored to job creation so redeliveries don't keep pushing it out
        await self.store.zadd(DEADLINES, jid, (job.created_at if job else time.time()) + self.timeout_s)
        if job is None:
            log.warning("result for unknown job %s (agent %s) — waiting for deadline", jid, result.agent)
            return
        have = set((await self.store.hgetall(f"agg:{jid}")).keys())
        expected = set(job.expected_agents or self.default_agents)
        if expected <= have:
            await self.flush(jid, timed_out=False)

    async def flush(self, jid: str, timed_out: bool) -> None:
        if not await self.store.set_nx(f"agg:done:{jid}", "1", ttl_s=TTL):
            return
        await self.store.zrem(DEADLINES, jid)
        job = await self._job(jid)
        if job is None:
            log.error("cannot flush %s: job record missing", jid)
            return
        results = [AgentResult.model_validate_json(v) for v in (await self.store.hgetall(f"agg:{jid}")).values()]
        status = {r.agent: ("ok" if not r.error else f"error: {r.error[:120]}") for r in results}
        for a in job.expected_agents or self.default_agents:
            status.setdefault(a, "timeout")
        raw = [f for r in results for f in r.findings]
        merged = merge_findings(raw)
        usage = Usage()
        for r in results:
            usage = usage.add(r.usage)
        await self.bus.publish(
            Topics.AGGREGATED,
            AggregatedReview(job=job, findings=merged, agents=status, agent_stats={r.agent: r.stats() for r in results},
                             usage=usage, raw_findings=len(raw)),
            key=jid,
        )
        await self.store.set(f"status:{jid}", "judging", ttl_s=TTL)
        await self.store.delete(f"agg:{jid}")
        log.info("flushed %s%s: %d raw → %d merged findings", jid, " (timeout)" if timed_out else "", len(raw),
                 len(merged))

    async def sweep_forever(self, interval_s: float = 5.0, stop: asyncio.Event | None = None) -> None:
        while not (stop and stop.is_set()):
            for jid in await self.store.zdue(DEADLINES, time.time()):
                log.warning("aggregation deadline passed for %s; flushing partial results", jid)
                await self.flush(jid, timed_out=True)
            await asyncio.sleep(interval_s)

    async def run(self, max_attempts: int = 3, stop: asyncio.Event | None = None) -> None:
        sweeper = asyncio.create_task(self.sweep_forever(stop=stop))
        try:
            await run_consumer(self.bus, service="aggregator", topics=[Topics.AGENT_RESULTS], group="aggregator",
                               handler=self.handle, max_attempts=max_attempts, stop=stop)
        finally:
            sweeper.cancel()
