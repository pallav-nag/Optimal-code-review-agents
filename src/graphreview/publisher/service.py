"""Publisher: posts the final review to GitHub (one review, inline comments) and stores it.

Inline comments are only allowed on lines inside the PR's diff hunks; anything else
(e.g. a broken caller in an untouched file — found via the graph) goes in the review body.
"""
from __future__ import annotations

import logging
import time

from graphreview.common.bus import Envelope, EventBus, Topics
from graphreview.common.github import GitHubClient
from graphreview.common.models import FinalReview, Finding, Severity, Verdict
from graphreview.common.state import StateStore
from graphreview.common.telemetry import REVIEW_E2E_SECONDS
from graphreview.common.worker import run_consumer

log = logging.getLogger(__name__)
ICON = {Severity.CRITICAL: "🔴", Severity.WARNING: "🟠", Severity.INFO: "🔵", Severity.NITPICK: "⚪"}
TTL = 30 * 24 * 3600


def comment_body(f: Finding) -> str:
    agents = ", ".join(f.reported_by or [f.agent])
    body = f"{ICON[f.severity]} **{f.title}** · _{f.category.value}, {f.severity.value}, confidence {f.confidence:.0%}_\n\n{f.message}"
    if f.suggestion:
        body += f"\n\n**Suggestion:** {f.suggestion}"
    if f.evidence:
        body += "\n\n<details><summary>Evidence</summary>\n\n" + "\n".join(f"- {e}" for e in f.evidence) + "\n</details>"
    return body + f"\n\n<sub>graphreview · {agents}</sub>"


def build_review(final: FinalReview) -> tuple[str, list[dict]]:
    commentable = {f.path: f.commentable_lines for f in final.job.files}
    inline, outside = [], []
    for f in final.findings:
        if f.line and f.line in commentable.get(f.file, set()):
            inline.append({"path": f.file, "line": f.line, "side": "RIGHT", "body": comment_body(f)})
        else:
            outside.append(f)
    counts = {s: sum(1 for f in final.findings if f.severity == s) for s in Severity}
    tally = " · ".join(f"{ICON[s]} {n} {s.value}" for s, n in counts.items() if n)
    body = [f"## GraphReview: {final.verdict.value.replace('_', ' ')}", "", final.summary or "", ""]
    if tally:
        body.append(tally)
    if outside:
        body += ["", "### Findings outside the diff"]
        body += [f"- `{f.file}{':' + str(f.line) if f.line else ''}` — {ICON[f.severity]} **{f.title}**: {f.message}"
                 for f in outside]
    agents = ", ".join(f"{a}: {s}" for a, s in sorted(final.agents.items()))
    u = final.usage
    body += ["", f"<sub>agents — {agents} · {u.llm_calls} LLM calls, {u.input_tokens + u.output_tokens:,} tokens "
                 f"({u.cache_read_tokens:,} cached) · {len(final.dropped)} finding(s) rejected by critic</sub>"]
    return "\n".join(body), inline


def render_markdown(final: FinalReview) -> str:
    """Console/CLI rendering of a review (inline comments listed under their file:line)."""
    body, _ = build_review(final)
    lines = [body, "", "### Inline comments"]
    for f in final.findings:
        lines.append(f"\n**{f.file}:{f.line}**\n{comment_body(f)}")
    return "\n".join(lines)


class Publisher:
    def __init__(self, bus: EventBus, store: StateStore, gh: GitHubClient | None, review_event: str = "COMMENT"):
        self.bus, self.store, self.gh, self.review_event = bus, store, gh, review_event

    async def handle(self, env: Envelope) -> None:
        final = FinalReview.model_validate_json(env.value)
        job = final.job
        await self.store.set(f"review:{job.job_id}", final.model_dump_json(), ttl_s=TTL)
        await self.store.set(f"status:{job.job_id}", "completed", ttl_s=TTL)
        if job.source == "github" and self.gh is not None:
            if await self.store.set_nx(f"posted:{job.job_id}", "1", ttl_s=TTL):
                body, comments = build_review(final)
                event = self.review_event if final.verdict == Verdict.REQUEST_CHANGES else "COMMENT"
                try:
                    await self.gh.create_review(job.repo, job.pr_number, commit_id=job.head_sha, body=body,
                                                event=event, comments=comments)
                except Exception:
                    await self.store.delete(f"posted:{job.job_id}")  # allow the retry to post
                    raise
                log.info("posted review on %s#%d (%d inline comments)", job.repo, job.pr_number, len(comments))
        REVIEW_E2E_SECONDS.observe(time.time() - job.created_at)

    async def run(self, max_attempts: int = 3, stop=None) -> None:
        await run_consumer(self.bus, service="publisher", topics=[Topics.COMPLETED], group="publisher",
                           handler=self.handle, max_attempts=max_attempts, stop=stop)
