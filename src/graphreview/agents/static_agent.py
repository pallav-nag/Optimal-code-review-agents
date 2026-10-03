"""Deterministic static analysis (ruff) restricted to lines the PR added.

Cheap, fast and precise — it catches undefined names, bugbear patterns and bandit-style
security smells so the LLM agents can spend their budget on semantic issues.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path

from graphreview.agents.tools import FileSource
from graphreview.common.models import AgentResult, Category, Finding, ReviewJob, Severity
from graphreview.common.telemetry import FINDINGS

log = logging.getLogger(__name__)
RULES = "F,E9,B,S,ASYNC,BLE,PERF"


def classify(code: str) -> tuple[Severity, Category]:
    if code.startswith("E9") or code in ("F821", "F822", "F823"):
        return Severity.CRITICAL, Category.BUG  # syntax error / NameError at runtime
    if code.startswith("S"):
        return (Severity.CRITICAL if code[1:3] in ("60", "30", "50", "10") else Severity.WARNING), Category.SECURITY
    if code.startswith(("F401", "F841")):
        return Severity.NITPICK, Category.MAINTAINABILITY
    if code.startswith(("B", "BLE", "F")):
        return Severity.WARNING, Category.BUG
    if code.startswith(("ASYNC", "PERF")):
        return Severity.INFO, Category.PERFORMANCE
    return Severity.INFO, Category.STYLE


class StaticAnalysisAgent:
    name = "static"

    def __init__(self, files: FileSource, ruff_bin: str | None = None):
        self.files = files
        venv_ruff = Path(sys.executable).parent / "ruff"
        self.ruff = ruff_bin or shutil.which("ruff") or (str(venv_ruff) if venv_ruff.exists() else None)

    async def review(self, job: ReviewJob) -> AgentResult:
        t0 = time.monotonic()
        targets = [f for f in job.files if f.path.endswith(".py") and f.status != "removed"]
        if not targets:
            return AgentResult(job_id=job.job_id, agent=self.name)
        if not self.ruff:
            return AgentResult(job_id=job.job_id, agent=self.name, error="ruff not installed")
        added = {f.path: set(f.added_lines) for f in targets}
        with tempfile.TemporaryDirectory() as tmp:
            written = []
            for f in targets:
                content = await self.files.read(job, f.path)
                if content is None:
                    continue
                p = Path(tmp, f.path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
                written.append(f.path)
            if not written:
                return AgentResult(job_id=job.job_id, agent=self.name, error="could not fetch file contents")
            proc = await asyncio.create_subprocess_exec(
                self.ruff, "check", "--isolated", "--no-cache", "--output-format", "json", "--select", RULES,
                "--exit-zero", *written, cwd=tmp, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        try:
            diags = json.loads(out or b"[]")
        except json.JSONDecodeError:
            return AgentResult(job_id=job.job_id, agent=self.name, error=f"ruff: {err.decode()[:300]}")
        findings = []
        for d in diags:
            path = Path(d["filename"]).resolve()
            rel = next((w for w in written if path.as_posix().endswith("/" + w)), None)
            row = d["location"]["row"]
            if rel is None or row not in added.get(rel, set()):
                continue  # pre-existing issue on an untouched line
            sev, cat = classify(d["code"])
            findings.append(Finding(
                agent=self.name, file=rel, line=row, end_line=d.get("end_location", {}).get("row", row),
                severity=sev, category=cat, title=f"{d['code']}: {d['message'][:80]}", message=d["message"],
                suggestion=(d.get("fix") or {}).get("message"), confidence=0.9,
                evidence=[f"ruff {d['code']} ({d.get('url') or 'n/a'})"],
            ))
            FINDINGS.labels(self.name, sev.value).inc()
        return AgentResult(job_id=job.job_id, agent=self.name, findings=findings,
                           duration_ms=int((time.monotonic() - t0) * 1000))
