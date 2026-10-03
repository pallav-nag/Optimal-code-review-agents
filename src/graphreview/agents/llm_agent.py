"""The agentic review loop.

Each specialist runs a manual tool-use loop against Claude: the model sees the diff plus a
graph impact report, calls GraphRAG tools to investigate (parallel tool calls run
concurrently), and finishes with schema-constrained JSON findings. The loop is bounded by
`max_turns`; the last turn disables tools so the model must answer.

Ablations (`job.context_mode`, used by the eval harness):
  graph_rag   impact report + tools                    (default)
  diff_only   just the diff, no tools
  full_files  diff + full contents of every changed file, no tools
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from graphreview.agents.specs import FINDINGS_SCHEMA, AgentSpec
from graphreview.agents.tools import FileSource, ToolBox, numbered
from graphreview.common.llm import LLM
from graphreview.common.models import AgentResult, Category, Finding, ReviewJob, Severity, Usage
from graphreview.common.telemetry import FINDINGS
from graphreview.context.client import ContextClient

log = logging.getLogger(__name__)
FULL_FILES_BUDGET = 60_000


def _usage_of(resp) -> Usage:
    u = resp.usage
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        llm_calls=1,
    )


def parse_findings(agent: str, text: str) -> list[Finding]:
    try:
        items = json.loads(text).get("findings", [])
    except (json.JSONDecodeError, AttributeError):
        log.warning("%s returned unparseable output: %.200s", agent, text)
        return []
    out = []
    for f in items:
        if not isinstance(f, dict) or not f.get("file"):
            continue
        try:
            sev = Severity(f.get("severity", "info"))
        except ValueError:
            sev = Severity.INFO
        try:
            cat = Category(f.get("category", "bug"))
        except ValueError:
            cat = Category.BUG
        line = f.get("line") if isinstance(f.get("line"), int) and f["line"] > 0 else None
        end = f.get("end_line") if isinstance(f.get("end_line"), int) and f["end_line"] >= (line or 0) else line
        out.append(Finding(
            agent=agent, file=f["file"].removeprefix("./"), line=line, end_line=end, severity=sev, category=cat,
            title=str(f.get("title", ""))[:200], message=str(f.get("message", "")),
            suggestion=f.get("suggestion") or None,
            confidence=min(max(float(f.get("confidence", 0.5) or 0.5), 0.0), 1.0),
            evidence=[str(e) for e in f.get("evidence", [])][:6],
        ))
    return out


class LLMAgent:
    def __init__(self, spec: AgentSpec, llm: LLM, ctx: ContextClient, files: FileSource, *,
                 model: str, max_turns: int = 8, max_tokens: int = 16_000):
        self.spec, self.llm, self.ctx, self.files = spec, llm, ctx, files
        self.model, self.max_turns, self.max_tokens = model, max_turns, max_tokens

    @property
    def name(self) -> str:
        return self.spec.name

    async def _initial_context(self, job: ReviewJob) -> str:
        if job.context_mode == "graph_rag":
            try:
                return f"<impact_report>\n{await self.ctx.impact(job.repo, job.files)}\n</impact_report>"
            except LookupError as e:
                return f"<impact_report>unavailable: {e}</impact_report>"
        if job.context_mode == "full_files":
            parts, used = [], 0
            for path in job.changed_paths:
                content = await self.files.read(job, path)
                if content is None:
                    continue
                block = f'<file path="{path}">\n{numbered(content)}\n</file>'
                if used + len(block) > FULL_FILES_BUDGET:
                    break
                parts.append(block)
                used += len(block)
            return "\n".join(parts)
        return ""

    async def review(self, job: ReviewJob) -> AgentResult:
        t0 = time.monotonic()
        usage = Usage()
        toolbox = ToolBox(self.name, job, self.ctx, self.files,
                          self.spec.tools if job.context_mode == "graph_rag" else [])
        context = await self._initial_context(job)
        prompt = (
            f"Repository: {job.repo}\nPR #{job.pr_number}: {job.title}\n\n"
            f"<pr_description>\n{job.body[:3000]}\n</pr_description>\n\n"
            f"{context}\n\n<diff>\n{job.annotated_diff()}\n</diff>"
        )
        messages: list[dict] = [{"role": "user", "content": prompt}]
        findings: list[Finding] = []
        for turn in range(self.max_turns):
            final = turn == self.max_turns - 1
            resp = await self.llm.create(
                agent=self.name, model=self.model, system=self.spec.system, messages=messages,
                output_schema=FINDINGS_SCHEMA, effort=self.spec.effort, tools=toolbox.defs or None,
                final_turn=final, max_tokens=self.max_tokens,
            )
            usage = usage.add(_usage_of(resp))
            if resp.stop_reason == "tool_use" and not final:
                messages.append({"role": "assistant", "content": resp.content})
                calls = [b for b in resp.content if b.type == "tool_use"]
                results = await asyncio.gather(*(toolbox.run(b.name, b.input) for b in calls))
                blocks: list[dict] = []
                for b, (text, is_err) in zip(calls, results, strict=True):
                    blk = {"type": "tool_result", "tool_use_id": b.id, "content": text}
                    if is_err:
                        blk["is_error"] = True
                    blocks.append(blk)
                if turn + 1 == self.max_turns - 1:
                    blocks.append({"type": "text", "text": "Investigation budget used up — return your findings now."})
                messages.append({"role": "user", "content": blocks})
                continue
            if resp.stop_reason == "max_tokens":
                raise RuntimeError("model hit max_tokens before finishing its findings")
            text = "".join(getattr(b, "text", "") for b in resp.content if b.type == "text")
            findings = parse_findings(self.name, text)
            break
        for f in findings:
            FINDINGS.labels(self.name, f.severity.value).inc()
        return AgentResult(
            job_id=job.job_id, agent=self.name, findings=findings, usage=usage, tool_calls=toolbox.counts,
            context_tokens=(len(prompt) + toolbox.chars_returned) // 4,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
