"""Whole pipeline in-process (gateway enqueue → 4 agents → aggregator → critic → publisher)."""
import json

from conftest import CASES, FIXTURE

from graphreview.common.bus import Topics
from graphreview.common.models import ReviewJob, Verdict
from graphreview.eval.harness import materialize, score
from graphreview.pipeline import LocalPipeline


async def test_seeded_sql_injection_is_caught_end_to_end(settings, tmp_path):
    case = json.loads((CASES / "01-sql-injection-email.json").read_text())
    head, files, expected = materialize(case, FIXTURE, tmp_path)
    async with LocalPipeline(settings) as pipe:
        idx = await pipe.index("eval/shopapp", FIXTURE)
        assert idx.graph_source == "builtin" and idx.chunks > 40
        job = ReviewJob(source="local", repo="eval/shopapp", title=case["title"], files=files, local_path=str(head))
        final = await pipe.review(job, timeout_s=30)

    assert final.verdict == Verdict.REQUEST_CHANGES
    assert set(final.agents) == {"static", "reviewer", "security", "tests"}
    assert all(v == "ok" for v in final.agents.values())
    assert score(final.findings, expected)["hit"] == 1
    sqli = next(f for f in final.findings if f.file == "shopapp/repository.py")
    assert {"security", "static"} <= set(sqli.reported_by)  # independent agents agreed → merged
    # every stage published exactly once for this job
    for topic in (Topics.REVIEW_REQUESTED, Topics.AGGREGATED, Topics.COMPLETED):
        assert len(pipe.bus.messages(topic)) == 1
    assert len(pipe.bus.messages(Topics.AGENT_RESULTS)) == 4


async def test_clean_change_is_approved(settings, tmp_path):
    case = json.loads((CASES / "11-clean-docstring.json").read_text())
    head, files, _ = materialize(case, FIXTURE, tmp_path)
    async with LocalPipeline(settings) as pipe:
        await pipe.index("eval/shopapp", FIXTURE)
        final = await pipe.review(ReviewJob(source="local", repo="eval/shopapp", title=case["title"], files=files,
                                            local_path=str(head)), timeout_s=30)
    assert [f for f in final.findings if f.category.value != "testing"] == []


def test_eval_cases_are_well_formed(tmp_path):
    for path in sorted(CASES.glob("*.json")):
        case = json.loads(path.read_text())
        assert {"id", "title", "edits", "expected"} <= set(case)
        _, files, expected = materialize(case, FIXTURE, tmp_path)
        assert files and all(f.patch.startswith("@@") for f in files)
        for x in expected:
            changed = {f.path: f.commentable_lines for f in files}
            # the primary anchor of each expected issue sits inside (or next to) a diff hunk
            assert any(abs(line - x.start) <= 3 for line in changed.get(x.file, [])), path.name
