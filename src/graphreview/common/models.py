"""Event and domain models shared by every service. All Kafka payloads are these models as JSON."""
from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from graphreview.common.diff import annotate_patch, parse_patch_lines


class ChangedFile(BaseModel):
    path: str
    status: str = "modified"  # added | modified | removed | renamed
    additions: int = 0
    deletions: int = 0
    patch: str = ""

    @property
    def added_lines(self) -> list[int]:
        return parse_patch_lines(self.patch).added

    @property
    def commentable_lines(self) -> set[int]:
        """Lines GitHub accepts for inline review comments (added + context lines in hunks)."""
        h = parse_patch_lines(self.patch)
        return set(h.added) | set(h.context)


ContextMode = Literal["graph_rag", "diff_only", "full_files"]


class ReviewJob(BaseModel):
    """Published to `pr.review.requested` when a PR is opened/updated."""

    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: Literal["github", "local"] = "github"
    repo: str  # "owner/name"
    pr_number: int = 0
    title: str = ""
    body: str = ""
    author: str = ""
    base_sha: str = ""
    head_sha: str = ""
    files: list[ChangedFile] = Field(default_factory=list)
    local_path: str | None = None  # source == "local": checkout on disk at head
    head_files: dict[str, str] = Field(default_factory=dict)  # PR-head contents shipped with the job (diff API)
    expected_agents: list[str] = Field(default_factory=list)
    context_mode: ContextMode = "graph_rag"  # ablation switch used by the eval harness
    created_at: float = Field(default_factory=time.time)

    @property
    def changed_paths(self) -> list[str]:
        return [f.path for f in self.files if f.status != "removed"]

    def annotated_diff(self, max_chars: int = 60_000) -> str:
        parts: list[str] = []
        used = 0
        for f in self.files:
            block = f"### {f.path} ({f.status}, +{f.additions}/-{f.deletions})\n{annotate_patch(f.patch)}\n"
            if used + len(block) > max_chars:
                parts.append(f"### {f.path} — omitted (diff budget exhausted; use read_snippet)\n")
                continue
            parts.append(block)
            used += len(block)
        return "\n".join(parts)


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    NITPICK = "nitpick"

    @property
    def rank(self) -> int:
        return {"critical": 3, "warning": 2, "info": 1, "nitpick": 0}[self.value]


class Category(StrEnum):
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    TESTING = "testing"
    STYLE = "style"


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    agent: str
    file: str
    line: int | None = None
    end_line: int | None = None
    severity: Severity = Severity.INFO
    category: Category = Category.BUG
    title: str
    message: str
    suggestion: str | None = None
    confidence: float = 0.7
    evidence: list[str] = Field(default_factory=list)  # graph/RAG facts that support the finding
    reported_by: list[str] = Field(default_factory=list)  # filled by the aggregator on merge


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    llm_calls: int = 0

    def add(self, other: Usage) -> Usage:
        return Usage(**{k: getattr(self, k) + getattr(other, k) for k in Usage.model_fields})


class AgentResult(BaseModel):
    """Published to `review.agent.results` by each agent."""

    job_id: str
    agent: str
    findings: list[Finding] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    tool_calls: dict[str, int] = Field(default_factory=dict)
    context_tokens: int = 0  # approx tokens of retrieved context sent to the model
    duration_ms: int = 0
    error: str | None = None

    def stats(self) -> dict:
        return {"status": "error" if self.error else "ok", "error": self.error, "duration_ms": self.duration_ms,
                "findings": len(self.findings), "tool_calls": self.tool_calls, "llm_calls": self.usage.llm_calls,
                "input_tokens": self.usage.input_tokens + self.usage.cache_read_tokens,
                "output_tokens": self.usage.output_tokens, "context_tokens": self.context_tokens}


class AggregatedReview(BaseModel):
    """Published to `review.aggregated` once all agents reported (or the deadline passed)."""

    job: ReviewJob
    findings: list[Finding]
    agents: dict[str, str]  # agent → "ok" | "error: …" | "timeout"
    agent_stats: dict[str, dict] = Field(default_factory=dict)  # agent → duration, tool calls, tokens, findings
    usage: Usage = Field(default_factory=Usage)
    raw_findings: int = 0


class Verdict(StrEnum):
    APPROVE = "approve"
    COMMENT = "comment"
    REQUEST_CHANGES = "request_changes"


class FinalReview(BaseModel):
    """Published to `review.completed` by the critic."""

    job: ReviewJob
    verdict: Verdict
    summary: str
    findings: list[Finding]
    dropped: list[Finding] = Field(default_factory=list)
    agents: dict[str, str] = Field(default_factory=dict)
    agent_stats: dict[str, dict] = Field(default_factory=dict)
    raw_findings: int = 0
    usage: Usage = Field(default_factory=Usage)
    completed_at: float = Field(default_factory=time.time)


class IndexRequest(BaseModel):
    """Published to `repo.index.requested` (push to default branch, or manual)."""

    repo: str
    clone_url: str | None = None
    ref: str | None = None
    local_path: str | None = None
    changed_paths: list[str] | None = None  # None → full reindex


class IndexResult(BaseModel):
    repo: str
    ref: str | None = None
    graph_nodes: int = 0
    graph_edges: int = 0
    graph_source: str = ""  # "graphify" | "builtin"
    chunks: int = 0
    duration_ms: int = 0
    indexed_at: float = Field(default_factory=time.time)
