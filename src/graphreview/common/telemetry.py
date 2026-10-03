"""Structured logging (JSON, with job_id correlation) and Prometheus metrics."""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import time

from prometheus_client import Counter, Histogram, start_http_server

job_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_id", default=None)

# ── Metrics ──────────────────────────────────────────────────────────────────
MESSAGES = Counter(
    "graphreview_messages_total", "Bus messages handled", ["service", "topic", "outcome"]
)
HANDLER_SECONDS = Histogram(
    "graphreview_handler_seconds", "Handler latency", ["service"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
LLM_TOKENS = Counter("graphreview_llm_tokens_total", "LLM tokens", ["agent", "kind"])
LLM_REQUESTS = Counter("graphreview_llm_requests_total", "LLM requests", ["agent", "model", "outcome"])
TOOL_CALLS = Counter("graphreview_tool_calls_total", "Agent tool calls", ["agent", "tool", "outcome"])
RETRIEVAL_SECONDS = Histogram(
    "graphreview_retrieval_seconds", "Retrieval latency", ["op"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
FINDINGS = Counter("graphreview_findings_total", "Findings emitted", ["stage", "severity"])
CRITIC_DROPPED = Counter("graphreview_critic_dropped_total", "Findings rejected by the critic", ["agent"])
REVIEW_E2E_SECONDS = Histogram(
    "graphreview_review_e2e_seconds", "Webhook → review published",
    buckets=(5, 10, 20, 30, 60, 90, 120, 180, 300, 600),
)
INDEX_SECONDS = Histogram(
    "graphreview_index_seconds", "Repo indexing time", ["stage"],
    buckets=(0.5, 1, 5, 10, 30, 60, 120, 300, 600),
)


def start_metrics(port: int) -> None:
    try:
        start_http_server(port)
    except OSError as e:  # port already bound (several workers in one process)
        logging.getLogger(__name__).debug("metrics server not started: %s", e)


# ── Logging ──────────────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if jid := job_id_var.get():
            payload["job_id"] = jid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


class _JobIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.job = f" [{jid[:8]}]" if (jid := job_id_var.get()) else ""
        return True


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.addFilter(_JobIdFilter())
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s%(job)s: %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("aiokafka", "httpx", "httpcore", "kafka", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
