import json

from conftest import REPO, ScriptedLLM, response, text_block, tool_block

from graphreview.agents.llm_agent import LLMAgent, parse_findings
from graphreview.agents.specs import SPECS
from graphreview.agents.static_agent import StaticAnalysisAgent, classify
from graphreview.agents.tools import LocalFileSource
from graphreview.common.models import ChangedFile, ReviewJob, Severity

PATCH = """@@ -19,3 +19,3 @@
 def compute_tax(amount_cents: int, region: str) -> int:
-    rate_bps = TAX_RATES_BPS.get(region, 0)
+    rate_bps = TAX_RATES_BPS[region]
     return (amount_cents * rate_bps + 5_000) // 10_000"""

FINDING = {"file": "shopapp/pricing.py", "line": 20, "end_line": 20, "severity": "warning", "category": "bug",
           "title": "KeyError for unknown region", "message": "Unknown regions now raise.", "suggestion": "",
           "confidence": 0.8, "evidence": ["checkout passes request.get('region', '')"]}


def job(**kw) -> ReviewJob:
    return ReviewJob(repo=REPO, pr_number=7, title="Strict tax lookup",
                     files=[ChangedFile(path="shopapp/pricing.py", patch=PATCH)], **kw)


async def test_agent_tool_loop(retriever):
    llm = ScriptedLLM([
        response([text_block("checking callers"), tool_block("get_callers", {"symbol": "compute_tax", "depth": 2}, "a"),
                  tool_block("search_docs", {"query": "tax rounding"}, "b")], "tool_use"),
        response([text_block(json.dumps({"findings": [FINDING]}))]),
    ])
    agent = LLMAgent(SPECS["reviewer"], llm, retriever, LocalFileSource(), model="claude-opus-5-5")
    result = await agent.review(job())

    assert [f.title for f in result.findings] == ["KeyError for unknown region"]
    assert result.tool_calls == {"get_callers": 1, "search_docs": 1}
    assert result.usage.llm_calls == 2 and result.usage.cache_read_tokens == 100
    first, second = llm.calls
    assert "<impact_report>" in first["messages"][0]["content"] and "order_total" in first["messages"][0]["content"]
    assert {t["name"] for t in first["tools"]} == set(SPECS["reviewer"].tools)
    assert all(t["strict"] for t in first["tools"])
    # both parallel tool results come back in ONE user message, matched by id
    results = second["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert "order_total" in results[0]["content"] and "CONTRIBUTING.md" in results[1]["content"]


async def test_last_turn_disables_tools(retriever):
    loop = [response([tool_block("search_code", {"query": "x"}, f"t{i}")], "tool_use") for i in range(2)]
    llm = ScriptedLLM([*loop, response([text_block('{"findings": []}')])])
    agent = LLMAgent(SPECS["security"], llm, retriever, LocalFileSource(), model="m", max_turns=3)
    result = await agent.review(job())
    assert result.findings == [] and len(llm.calls) == 3
    assert llm.calls[-1]["final_turn"] is True
    assert llm.calls[-1]["messages"][-1]["content"][-1]["type"] == "text"  # "budget used up" nudge


async def test_tool_errors_are_reported_not_raised(retriever):
    llm = ScriptedLLM([
        response([tool_block("read_snippet", {"path": "nope.py", "start_line": 1, "end_line": 5}),
                  ], "tool_use"),
        response([text_block('{"findings": []}')]),
    ])
    agent = LLMAgent(SPECS["reviewer"], llm, retriever, LocalFileSource(), model="m")
    await agent.review(job())
    res = llm.calls[1]["messages"][-1]["content"][0]
    assert "not found" in res["content"]


async def test_diff_only_mode_sends_no_tools(retriever):
    llm = ScriptedLLM([response([text_block('{"findings": []}')])])
    agent = LLMAgent(SPECS["reviewer"], llm, retriever, LocalFileSource(), model="m")
    await agent.review(job(context_mode="diff_only"))
    assert llm.calls[0]["tools"] is None and "<impact_report>" not in llm.calls[0]["messages"][0]["content"]


def test_parse_findings_is_defensive():
    raw = json.dumps({"findings": [FINDING, {"file": ""}, {"file": "a.py", "line": -3, "severity": "bogus",
                                                           "category": "??", "title": "t", "message": "m"}]})
    out = parse_findings("x", raw)
    assert len(out) == 2
    assert out[1].line is None and out[1].severity == Severity.INFO
    assert parse_findings("x", "not json") == []


async def test_static_agent_only_reports_added_lines(tmp_path):
    (tmp_path / "m.py").write_text("import os\n\n\ndef f():\n    return undefined_thing\n")
    patch = "@@ -3,1 +3,3 @@\n \n+def f():\n+    return undefined_thing"
    j = ReviewJob(repo=REPO, files=[ChangedFile(path="m.py", patch=patch)], local_path=str(tmp_path))
    result = await StaticAnalysisAgent(LocalFileSource()).review(j)
    assert result.error is None
    codes = {f.title.split(":")[0] for f in result.findings}
    assert codes == {"F821"}  # F401 `import os` is on an untouched line → ignored
    assert result.findings[0].line == 5


def test_static_severity_mapping():
    assert classify("F821")[0] == Severity.CRITICAL
    assert classify("S608")[1].value == "security"
    assert classify("F401")[0] == Severity.NITPICK


async def test_local_file_source_blocks_traversal(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n")
    j = ReviewJob(repo=REPO, local_path=str(tmp_path / "."))
    src = LocalFileSource()
    assert await src.read(j, "ok.py") == "x = 1\n"
    assert await src.read(j, "../../etc/passwd") is None
