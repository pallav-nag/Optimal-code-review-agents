"""AnthropicLLM through the real SDK, with the HTTP layer mocked: checks what goes on the wire
and that the agent loop handles real response objects (thinking + tool_use blocks)."""
import json

import anthropic
import httpx2
from conftest import REPO

from graphreview.agents.llm_agent import LLMAgent
from graphreview.agents.specs import FINDINGS_SCHEMA, SPECS
from graphreview.agents.tools import LocalFileSource
from graphreview.common.llm import AnthropicLLM, LLMRefusal
from graphreview.common.models import ChangedFile, ReviewJob

USAGE = {"input_tokens": 1200, "output_tokens": 80, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0}


def message(content, stop_reason):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
            "stop_reason": stop_reason, "stop_sequence": None, "usage": USAGE}


def mock_llm(responses):
    sent = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append({"body": json.loads(request.content), "headers": dict(request.headers)})
        return httpx2.Response(200, json=responses.pop(0))

    client = anthropic.AsyncAnthropic(api_key="test", max_retries=0,
                                      http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    return AnthropicLLM(client=client), sent


async def test_request_shape_and_tool_loop(retriever):
    final = {"findings": [{"file": "shopapp/pricing.py", "line": 19, "end_line": 19, "severity": "critical",
                           "category": "bug", "title": "Callers not updated",
                           "message": "order_total still calls compute_tax(discounted, region).",
                           "suggestion": "", "confidence": 0.9, "evidence": ["order_total → compute_tax"]}]}
    llm, sent = mock_llm([
        message([{"type": "thinking", "thinking": "", "signature": "sig"},
                 {"type": "tool_use", "id": "toolu_1", "name": "get_callers",
                  "input": {"symbol": "compute_tax", "depth": 1}}], "tool_use"),
        message([{"type": "text", "text": json.dumps(final)}], "end_turn"),
    ])
    patch = "@@ -19,2 +19,2 @@\n-def compute_tax(amount_cents: int, region: str) -> int:\n+def compute_tax(amount_cents: int, region: str, rates: dict) -> int:\n     x"
    job = ReviewJob(repo=REPO, files=[ChangedFile(path="shopapp/pricing.py", patch=patch)])
    result = await LLMAgent(SPECS["reviewer"], llm, retriever, LocalFileSource(), model="claude-opus-5-5").review(job)

    assert [f.title for f in result.findings] == ["Callers not updated"]
    assert result.usage.cache_read_tokens == 1800 and result.usage.llm_calls == 2
    first, second = sent[0]["body"], sent[1]["body"]
    assert first["model"] == "claude-opus-5-5"
    assert first["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": FINDINGS_SCHEMA}}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in sent[0]["headers"]["anthropic-beta"]
    assert "thinking" not in first and "temperature" not in first  # Opus 5.5: adaptive by default, no sampling params
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in first["tools"])
    # turn 2 replays the assistant turn verbatim (thinking block included) + the tool result
    assert second["messages"][1]["content"][0]["type"] == "thinking"
    tool_result = second["messages"][2]["content"][0]
    assert tool_result["tool_use_id"] == "toolu_1" and "order_total" in tool_result["content"]


async def test_refusal_raises():
    llm, _ = mock_llm([message([], "refusal")])
    try:
        await llm.create(agent="x", model="claude-opus-5-5", system="s", messages=[{"role": "user", "content": "hi"}],
                         output_schema={"type": "object"})
    except LLMRefusal:
        return
    raise AssertionError("expected LLMRefusal")
