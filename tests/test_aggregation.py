import json
import time

from conftest import ScriptedLLM, response, text_block

from graphreview.aggregator.service import DEADLINES, Aggregator, merge_findings
from graphreview.common.bus import Envelope, MemoryBus, Topics
from graphreview.common.models import (
    AgentResult,
    AggregatedReview,
    Category,
    ChangedFile,
    FinalReview,
    Finding,
    ReviewJob,
    Severity,
    Verdict,
)
from graphreview.common.state import MemoryStore
from graphreview.critic.service import Critic
from graphreview.publisher.service import build_review


def f(agent, line, sev="warning", cat="bug", title="Null deref", conf=0.7, file="a.py"):
    return Finding(agent=agent, file=file, line=line, severity=Severity(sev), category=Category(cat), title=title,
                   message=title, confidence=conf)


def test_merge_clusters_and_boosts_consensus():
    merged = merge_findings([
        f("reviewer", 10), f("security", 12, sev="critical"), f("static", 40),
        f("tests", 11, cat="testing", title="Add a regression test for empty input"),
    ])
    assert len(merged) == 3
    top = next(m for m in merged if m.line == 12)
    assert top.severity == Severity.CRITICAL and top.reported_by == ["reviewer", "security"]
    assert top.confidence > 0.7  # agreement raises confidence


async def test_aggregator_flushes_when_all_agents_report():
    bus, store = MemoryBus(), MemoryStore()
    job = ReviewJob(repo="o/r", pr_number=3, expected_agents=["a", "b"])
    await store.set(f"job:{job.job_id}", job.model_dump_json())
    agg = Aggregator(bus, store, timeout_s=60)
    for name in ("a", "b", "a"):  # duplicate delivery of "a" is harmless
        r = AgentResult(job_id=job.job_id, agent=name, findings=[f(name, 5)])
        await agg.handle(Envelope(Topics.AGENT_RESULTS, job.job_id, r.model_dump_json().encode()))
    out = bus.messages(Topics.AGGREGATED)
    assert len(out) == 1
    review = AggregatedReview.model_validate_json(out[0].value)
    assert review.job.pr_number == 3 and review.raw_findings == 2 and len(review.findings) == 1
    assert review.agents == {"a": "ok", "b": "ok"}


async def test_aggregator_timeout_flushes_partial():
    bus, store = MemoryBus(), MemoryStore()
    job = ReviewJob(repo="o/r", expected_agents=["a", "b"], created_at=time.time() - 100)
    await store.set(f"job:{job.job_id}", job.model_dump_json())
    agg = Aggregator(bus, store, timeout_s=10)
    r = AgentResult(job_id=job.job_id, agent="a", error="rate limited")
    await agg.handle(Envelope(Topics.AGENT_RESULTS, job.job_id, r.model_dump_json().encode()))
    assert not bus.messages(Topics.AGGREGATED)
    for jid in await store.zdue(DEADLINES, time.time()):
        await agg.flush(jid, timed_out=True)
    review = AggregatedReview.model_validate_json(bus.messages(Topics.AGGREGATED)[0].value)
    assert review.agents["b"] == "timeout" and review.agents["a"].startswith("error")


async def test_critic_filters_and_static_bypasses():
    job = ReviewJob(repo="o/r", files=[ChangedFile(path="a.py", patch="@@ -1,1 +1,2 @@\n x\n+y")])
    keep, drop, static = f("reviewer", 2), f("security", 30, title="Speculative"), f("static", 2, title="F821")
    llm = ScriptedLLM([response([text_block(json.dumps({
        "summary": "s", "verdict": "request_changes",
        "judgments": [{"id": keep.id, "keep": True, "confidence": 0.9, "severity": "critical", "reason": "ok"},
                      {"id": drop.id, "keep": True, "confidence": 0.3, "severity": "info", "reason": "weak"}]}))])])
    critic = Critic(MemoryBus(), llm, None, model="m", min_confidence=0.6)
    final = await critic.judge(AggregatedReview(job=job, findings=[keep, drop, static], agents={}))
    assert {x.id for x in final.findings} == {keep.id, static.id}
    assert [x.id for x in final.dropped] == [drop.id]
    assert final.findings[0].severity == Severity.CRITICAL and final.verdict == Verdict.REQUEST_CHANGES
    assert static.id not in json.dumps(llm.calls[0]["messages"])  # deterministic findings aren't re-judged


def test_publisher_splits_inline_and_out_of_diff():
    job = ReviewJob(repo="o/r", files=[ChangedFile(path="a.py", patch="@@ -1,1 +1,2 @@\n x\n+y")])
    final = FinalReview(job=job, verdict=Verdict.COMMENT, summary="ok",
                        findings=[f("reviewer", 2), f("reviewer", 9, file="caller.py", title="Caller not updated")])
    body, comments = build_review(final)
    assert [(c["path"], c["line"], c["side"]) for c in comments] == [("a.py", 2, "RIGHT")]
    assert "Findings outside the diff" in body and "caller.py:9" in body
