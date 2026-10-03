"""LLM access layer.

`AnthropicLLM` calls Claude through the official SDK with:
  * structured output (`output_config.format`) so the final answer is schema-valid JSON,
  * explicit `effort` per agent role,
  * top-level prompt caching — the system prompt + tool schemas + growing tool-loop
    prefix are re-read from cache on each turn of the agent loop,
  * server-side refusal fallback (beta) so a declined request is re-run on a fallback model.

`FakeLLM` is a deterministic, rule-based stand-in with the same response shape. It lets the
whole pipeline (tool loop included) run in CI and in the offline demo with no API key.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import uuid
from types import SimpleNamespace
from typing import Any, Protocol

from graphreview.common.telemetry import LLM_REQUESTS, LLM_TOKENS


class LLMRefusal(Exception):
    pass


class LLM(Protocol):
    async def create(self, *, agent: str, model: str, system: str, messages: list[dict],
                     output_schema: dict, effort: str = "high", tools: list[dict] | None = None,
                     final_turn: bool = False, max_tokens: int = 16_000) -> Any: ...


def _record_usage(agent: str, usage: Any) -> None:
    LLM_TOKENS.labels(agent, "input").inc(getattr(usage, "input_tokens", 0) or 0)
    LLM_TOKENS.labels(agent, "output").inc(getattr(usage, "output_tokens", 0) or 0)
    LLM_TOKENS.labels(agent, "cache_read").inc(getattr(usage, "cache_read_input_tokens", 0) or 0)
    LLM_TOKENS.labels(agent, "cache_write").inc(getattr(usage, "cache_creation_input_tokens", 0) or 0)


class AnthropicLLM:
    def __init__(self, fallbacks: bool = True, max_retries: int = 4, client=None):
        import anthropic

        self._anthropic = anthropic
        self.client = client or anthropic.AsyncAnthropic(max_retries=max_retries)
        self.fallbacks = fallbacks

    async def create(self, *, agent, model, system, messages, output_schema, effort="high", tools=None,
                     final_turn=False, max_tokens=16_000):
        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            cache_control={"type": "ephemeral"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": output_schema}},
        )
        if tools:
            kwargs["tools"] = tools
            if final_turn:
                kwargs["tool_choice"] = {"type": "none"}
        if self.fallbacks:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        try:
            resp = await self.client.beta.messages.create(**kwargs)
        except self._anthropic.APIStatusError as e:
            LLM_REQUESTS.labels(agent, model, f"http_{e.status_code}").inc()
            raise
        except self._anthropic.APIConnectionError:
            LLM_REQUESTS.labels(agent, model, "connection_error").inc()
            raise
        LLM_REQUESTS.labels(agent, model, resp.stop_reason or "unknown").inc()
        _record_usage(agent, resp.usage)
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise LLMRefusal(getattr(details, "category", None) or "refused")
        return resp


# ── Deterministic fake ───────────────────────────────────────────────────────

_ADDED_LINE = re.compile(r"^\s*(\d+) \+(.*)$")
_FILE_HDR = re.compile(r"^### (\S+) \(")

# (regex on an added line, title, message, severity, category)
_SECURITY_RULES = [
    (r"(execute|executemany|query)\(\s*f[\"']|(execute|executemany|query)\(.*[\"']\s*(%|\.format\()", "SQL injection",
     "Query is built with string formatting from caller-controlled values. Use parameterized queries.",
     "critical", "security"),
    (r"\beval\(|\bexec\(", "Arbitrary code execution", "eval/exec on dynamic input allows code execution.",
     "critical", "security"),
    (r"pickle\.loads?\(", "Unsafe deserialization", "pickle on untrusted data allows code execution.",
     "critical", "security"),
    (r"shell\s*=\s*True", "Shell injection risk", "subprocess with shell=True and interpolated input.",
     "critical", "security"),
    (r"verify\s*=\s*False", "TLS verification disabled", "Certificate verification is disabled.",
     "warning", "security"),
    (r"(password|secret|api_key|token)\s*=\s*[\"'][^\"']{6,}[\"']", "Hard-coded secret",
     "Credential literal committed to source.", "critical", "security"),
    (r"hashlib\.(md5|sha1)\(", "Weak hash", "MD5/SHA1 is not suitable for security purposes.",
     "warning", "security"),
]
_REVIEW_RULES = [
    (r"<=\s*len\(", "Possible off-by-one", "Comparing an index with `<= len(...)` reads one past the end.",
     "warning", "bug"),
    (r"^\s*except\s*:", "Bare except", "Bare `except:` swallows KeyboardInterrupt/SystemExit and hides bugs.",
     "warning", "maintainability"),
    (r"except\s+Exception\s*:\s*pass|except\s+Exception\s*:\s*$", "Swallowed exception",
     "Exception is caught and ignored; failures become silent.", "warning", "bug"),
    (r"[=!]=\s*None", "Comparison to None", "Use `is None` / `is not None`.", "nitpick", "style"),
    (r"def \w+\(.*=\s*(\[\]|\{\})", "Mutable default argument",
     "Default list/dict is shared across calls.", "warning", "bug"),
    (r"requests\.(get|post|put|delete)\((?!.*timeout)", "HTTP call without timeout",
     "Requests without a timeout can hang a worker indefinitely.", "warning", "performance"),
    (r"time\.sleep\(", "Blocking sleep", "Blocking sleep; in async code use `await asyncio.sleep`.",
     "info", "performance"),
    (r"/\s*len\(", "Possible division by zero", "Divides by a length that can be zero.", "warning", "bug"),
]


def _added_lines(diff_text: str) -> list[tuple[str, int, str]]:
    out, current = [], None
    for raw in diff_text.splitlines():
        if m := _FILE_HDR.match(raw):
            current = m.group(1)
        elif current and (m := _ADDED_LINE.match(raw)):
            out.append((current, int(m.group(1)), m.group(2)))
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))


class FakeLLM:
    """Rule-based reviewer that speaks the Messages API response shape."""

    def __init__(self, latency_s: float = 0.0):
        self.calls: list[dict] = []
        self.latency_s = latency_s

    @staticmethod
    def _used_tool(messages: list[dict]) -> bool:
        return any(
            isinstance(m["content"], list) and any(isinstance(c, dict) and c.get("type") == "tool_result"
                                                   for c in m["content"])
            for m in messages
        )

    def _resp(self, content: list, stop_reason: str, prompt_chars: int, out_chars: int):
        return SimpleNamespace(
            content=content,
            stop_reason=stop_reason,
            usage=SimpleNamespace(input_tokens=prompt_chars // 4, output_tokens=max(out_chars // 4, 1),
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )

    async def create(self, *, agent, model, system, messages, output_schema, effort="high", tools=None,
                     final_turn=False, max_tokens=16_000):
        self.calls.append({"agent": agent, "messages": messages, "tools": [t["name"] for t in tools or []]})
        if self.latency_s:
            await asyncio.sleep(self.latency_s * random.uniform(0.7, 1.3))  # noqa: S311 — jitter, not crypto
        prompt = system + "".join(_text_of(m["content"]) for m in messages if m["role"] == "user")
        _record_usage(agent, SimpleNamespace(input_tokens=len(prompt) // 4, output_tokens=50))
        LLM_REQUESTS.labels(agent, "fake", "tool_use" if tools and not final_turn and not self._used_tool(messages)
                            else "end_turn").inc()
        used_tool = self._used_tool(messages)
        tool_names = {t["name"] for t in tools or []}
        if tool_names and not used_tool and not final_turn:
            preferred = [("search_docs", {"query": "coding conventions"}), ("find_tests", {"symbol": "main"}),
                         ("search_code", {"query": "error handling"})]
            name, args = next(((n, a) for n, a in preferred if n in tool_names), (sorted(tool_names)[0], {}))
            block = SimpleNamespace(type="tool_use", id=f"toolu_{uuid.uuid4().hex[:12]}", name=name, input=args)
            return self._resp([block], "tool_use", len(prompt), 40)

        diff_text = _text_of(messages[0]["content"])
        if agent == "critic":
            payload = self._critic(diff_text)
        else:
            payload = {"findings": self._findings(agent, diff_text)}
        text = json.dumps(payload)
        return self._resp([SimpleNamespace(type="text", text=text)], "end_turn", len(prompt), len(text))

    def _findings(self, agent: str, diff_text: str) -> list[dict]:
        rules = {"security": _SECURITY_RULES, "reviewer": _REVIEW_RULES}.get(agent, [])
        found = []
        added = _added_lines(diff_text)
        for path, line, code in added:
            for pattern, title, message, severity, category in rules:
                if re.search(pattern, code):
                    found.append(dict(file=path, line=line, end_line=line, severity=severity, category=category,
                                      title=title, message=message, suggestion=None, confidence=0.75,
                                      evidence=[f"{path}:{line}: {code.strip()[:120]}"]))
        if agent == "tests":
            # trust the graph when it's there: only flag code no test reaches
            untested = "appears untested" in diff_text or "<impact_report>" not in diff_text
            touched_tests = any("test" in p for p, _, _ in added)
            for path, line, code in added:
                if untested and not touched_tests and (m := re.match(r"\s*def (\w+)\(", code)) and "test" not in path:
                    found.append(dict(file=path, line=line, end_line=line, severity="info", category="testing",
                                      title=f"No test for `{m.group(1)}`",
                                      message=f"`{m.group(1)}` is new or changed but no test in the PR exercises it.",
                                      suggestion=f"Add a unit test covering `{m.group(1)}` edge cases.",
                                      confidence=0.6, evidence=[]))
        return found

    def _critic(self, prompt: str) -> dict:
        ids = re.findall(r'"id":\s*"(\w+)"', prompt)
        sev = re.findall(r'"severity":\s*"(\w+)"', prompt)
        verdict = "request_changes" if "critical" in sev else ("comment" if ids else "approve")
        return {
            "summary": "Reviewed by the offline rule engine (LLM_BACKEND=fake) — set LLM_BACKEND=anthropic for Claude.",
            "verdict": verdict,
            "judgments": [{"id": i, "keep": True, "confidence": 0.8, "severity": s, "reason": "rule match"}
                          for i, s in zip(ids, sev, strict=False)],
        }


def make_llm(settings) -> LLM:
    if settings.llm_backend == "fake":
        return FakeLLM(settings.fake_llm_latency_s)
    return AnthropicLLM(fallbacks=settings.llm_fallbacks)
