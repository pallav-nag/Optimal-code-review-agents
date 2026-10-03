"""Tools exposed to the LLM agents, and their executor.

Changed files are read at the PR head (GitHub contents API or the local checkout); everything
else comes from the base-branch index via the context service.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from graphreview.common.models import ReviewJob
from graphreview.common.telemetry import TOOL_CALLS
from graphreview.context.client import ContextClient

log = logging.getLogger(__name__)
MAX_TOOL_CHARS = 8_000


def _tool(name: str, description: str, props: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False},
    }


TOOL_DEFS: dict[str, dict] = {
    t["name"]: t
    for t in [
        _tool("get_callers",
              "Code-graph lookup: functions/methods that call `symbol` (up to `depth` hops, 1-3) and what it calls. "
              "Use to check whether a changed signature or behaviour breaks callers outside the diff.",
              {"symbol": {"type": "string", "description": "Function, method or class name"},
               "depth": {"type": "integer", "description": "Hops, 1-3"}}),
        _tool("find_tests",
              "Code-graph lookup: tests that transitively exercise `symbol`.",
              {"symbol": {"type": "string"}}),
        _tool("search_code",
              "Hybrid semantic + keyword search over the repository's code (base branch). Use to find similar "
              "patterns, existing helpers, or how other call sites handle the same concern.",
              {"query": {"type": "string"}}),
        _tool("search_docs",
              "Search the repository's own docs (CONTRIBUTING, ADRs, READMEs) for conventions relevant to the change.",
              {"query": {"type": "string"}}),
        _tool("read_snippet",
              "Read lines [start_line, end_line] of a file (max 200 lines). Files changed in the PR are read at the "
              "PR head; other files from the base branch.",
              {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}),
    ]
}


class FileSource(Protocol):
    async def read(self, job: ReviewJob, path: str) -> str | None: ...


class LocalFileSource:
    async def read(self, job, path):
        if path in job.head_files:
            return job.head_files[path]
        if not job.local_path:
            return None
        root = Path(job.local_path).resolve()
        p = (root / path).resolve()
        if not p.is_relative_to(root) or not p.is_file():  # no path traversal out of the checkout
            return None
        return p.read_text(encoding="utf-8", errors="replace")


class GitHubFileSource:
    def __init__(self, gh):
        self.gh = gh
        self._cache: dict[tuple[str, str], str | None] = {}

    async def read(self, job, path):
        if job.local_path or path in job.head_files:
            return await LocalFileSource().read(job, path)
        if not job.head_sha:
            return None
        key = (job.head_sha, path)
        if key not in self._cache:
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[key] = await self.gh.get_file(job.repo, path, job.head_sha)
        return self._cache[key]


def numbered(text: str, start: int = 1, end: int | None = None) -> str:
    lines = text.splitlines()
    end = min(end or len(lines), len(lines))
    return "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(max(start, 1), end + 1))


class ToolBox:
    def __init__(self, agent: str, job: ReviewJob, ctx: ContextClient, files: FileSource, allowed: list[str]):
        self.agent, self.job, self.ctx, self.files = agent, job, ctx, files
        self.allowed = [a for a in allowed if a in TOOL_DEFS]
        self.counts: dict[str, int] = {}
        self.chars_returned = 0

    @property
    def defs(self) -> list[dict]:
        return [TOOL_DEFS[n] for n in self.allowed]

    async def run(self, name: str, args: dict) -> tuple[str, bool]:
        self.counts[name] = self.counts.get(name, 0) + 1
        if name not in self.allowed:
            TOOL_CALLS.labels(self.agent, name, "unknown").inc()
            return f"Unknown tool {name!r}.", True
        try:
            text = await self._dispatch(name, args)
            TOOL_CALLS.labels(self.agent, name, "ok").inc()
            ok = True
        except LookupError as e:
            text, ok = f"Not available: {e}", False
            TOOL_CALLS.labels(self.agent, name, "not_found").inc()
        except Exception as e:
            log.warning("tool %s failed: %r", name, e)
            text, ok = f"Tool error: {e!r}", False
            TOOL_CALLS.labels(self.agent, name, "error").inc()
        if len(text) > MAX_TOOL_CHARS:
            text = text[:MAX_TOOL_CHARS] + "\n… (truncated)"
        self.chars_returned += len(text)
        return text, not ok

    async def _dispatch(self, name: str, a: dict) -> str:
        repo = self.job.repo
        if name == "get_callers":
            return await self.ctx.callers(repo, a["symbol"], max(1, min(int(a.get("depth", 1)), 3)))
        if name == "find_tests":
            return await self.ctx.find_tests(repo, a["symbol"])
        if name == "search_code":
            return await self.ctx.search_code(repo, a["query"], 5)
        if name == "search_docs":
            return await self.ctx.search_docs(repo, a["query"], 4)
        if name == "read_snippet":
            path, start = a["path"], int(a["start_line"])
            end = min(int(a["end_line"]), start + 200)
            if path in self.job.changed_paths:
                content = await self.files.read(self.job, path)
                if content is not None:
                    return numbered(content, start, end)
            return await self.ctx.read_snippet(repo, path, start, end)
        raise ValueError(name)
