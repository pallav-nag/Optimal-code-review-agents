"""Runtime configuration, read from environment variables (and `.env` when present)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Messaging / state ────────────────────────────────────────────
    bus_backend: Literal["kafka", "memory"] = "kafka"
    kafka_bootstrap: str = "localhost:9092"
    kafka_max_poll_interval_ms: int = 600_000  # LLM agents can take minutes per message
    state_backend: Literal["redis", "memory"] = "redis"
    redis_url: str = "redis://localhost:6379/0"

    # ── Retrieval ────────────────────────────────────────────────────
    qdrant_url: str | None = None  # None → embedded in-memory Qdrant (dev/tests)
    qdrant_collection: str = "code_chunks"
    embedding_backend: Literal["fastembed", "hash"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    sparse_model: str = "Qdrant/bm25"
    context_service_url: str = "http://localhost:8001"
    repos_dir: str = "/tmp/graphreview/repos"  # noqa: S108 — containers mount a volume here
    graphify_enabled: bool = True
    graphify_timeout_s: int = 600

    # ── LLM ──────────────────────────────────────────────────────────
    llm_backend: Literal["anthropic", "fake"] = "anthropic"
    fake_llm_latency_s: float = 0.0  # simulate model latency with the fake backend (demos: visible progress, KEDA lag)
    agent_model: str = "claude-opus-5-5"
    critic_model: str = "claude-opus-5-5"
    llm_fallbacks: bool = True  # server-side refusal fallback (beta)
    max_agent_turns: int = 8
    llm_max_tokens: int = 16_000
    critic_min_confidence: float = 0.6

    # ── Pipeline ─────────────────────────────────────────────────────
    expected_agents: list[str] = Field(
        default_factory=lambda: ["static", "reviewer", "security", "tests"]
    )
    aggregation_timeout_s: int = 300
    handler_max_attempts: int = 3

    # ── GitHub ───────────────────────────────────────────────────────
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    github_webhook_secret: str | None = None
    allow_unsigned_webhooks: bool = False  # only for local dev
    github_review_event: Literal["COMMENT", "REQUEST_CHANGES"] = "COMMENT"

    # ── Demo ─────────────────────────────────────────────────────────
    demo_mode: bool = False  # web UI buttons: index the sample repo, submit seeded-bug PRs
    demo_dir: str = "eval"  # contains cases/ and fixtures/shopapp

    # ── Ops ──────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_json: bool = True
    metrics_port: int = 9100


@lru_cache
def get_settings() -> Settings:
    return Settings()
