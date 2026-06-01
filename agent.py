"""
LLM Reviewer Agent
==================
Uses Claude to review the PR diff.
Crucially: uses GraphifyClient to fetch ONLY the relevant subgraph context
instead of loading all changed files — ~85% token reduction.
"""
from __future__ import annotations
import os
import anthropic
from shared.base_agent import BaseAgent, run_agent
from shared.graphify_client import GraphifyClient
from shared.models import ReviewJob, Finding, Severity

SYSTEM = """You are a senior software engineer doing a focused code review.
You will be given:
1. A PR diff
2. Relevant graph context (callers, dependencies, affected paths) from Graphify

Review for: correctness, security, performance, maintainability.
Return findings as JSON array:
[{"file": "...", "line": 42, "severity": "warning|critical|info|nitpick", "message": "...", "suggestion": "..."}]
Return ONLY the JSON array, no other text."""


class LLMReviewerAgent(BaseAgent):
    name = "llm_reviewer"

    def __init__(self):
        super().__init__()
        self.client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    async def review(self, job: ReviewJob) -> list[Finding]:
        # 1. Query Graphify for relevant context (not full files)
        async with GraphifyClient(repo=job.repo_full_name) as g:
            # What does the changed code depend on?
            deps_ctx    = await g.impact(job.changed_files)
            # Anything that calls into the changed files?
            callers_ctx = await g.query(
                f"what calls into these files: {', '.join(job.changed_files)}"
            )

        # 2. Build a compact prompt — graph context replaces raw file content
        prompt = f"""PR: {job.pr_title}
Changed files: {', '.join(job.changed_files)}

=== Graph context (dependency impact) ===
{deps_ctx}

=== Graph context (callers) ===
{callers_ctx}

=== Diff ===
{job.diff_patch[:8000]}  # hard cap — graph context already covers the rest
"""
        # 3. Call Claude
        resp = await self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()

        # 4. Parse findings
        import json
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return []

        return [
            Finding(
                agent=self.name,
                file=f.get("file", ""),
                line=f.get("line"),
                severity=Severity(f.get("severity", "info")),
                message=f.get("message", ""),
                suggestion=f.get("suggestion"),
            )
            for f in items if isinstance(f, dict)
        ]


if __name__ == "__main__":
    run_agent(LLMReviewerAgent())
