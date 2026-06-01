"""Shared Pydantic models — imported by all agents and services."""
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field
import uuid, time


class ReviewJob(BaseModel):
    """Enqueued when a PR is opened / updated."""
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    repo_full_name: str          # "owner/repo"
    pr_number: int
    pr_title: str
    base_sha: str
    head_sha: str
    changed_files: list[str]
    diff_patch: str              # raw unified diff
    enqueued_at: float = Field(default_factory=time.time)


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING  = "warning"
    INFO     = "info"
    NITPICK  = "nitpick"


class Finding(BaseModel):
    agent: str                   # which agent produced this
    file: str
    line: int | None = None
    severity: Severity
    message: str
    suggestion: str | None = None
    confidence: float = 1.0      # 0-1, set by critic


class AgentResult(BaseModel):
    job_id: str
    agent: str
    findings: list[Finding]
    token_usage: int = 0         # for cost tracking
    duration_ms: int = 0
    graph_queries: int = 0       # how many graphify queries were made


class AggregatedResult(BaseModel):
    job_id: str
    repo_full_name: str
    pr_number: int
    findings: list[Finding]      # merged, deduped, scored
    total_tokens: int = 0
    ready_to_post: bool = False
