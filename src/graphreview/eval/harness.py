"""Seeded-bug benchmark.

Each case applies find/replace edits to the fixture repo (base = the indexed repo, head =
the edited copy), producing a realistic PR diff with a neutral title that doesn't reveal
the bug. Expected issues are anchored to text in the head version, so line numbers are
computed, never hand-maintained.

Scoring (per context mode):
  recall     expected issues matched by ≥1 final finding (same file, line within ±3)
  precision  defect findings (category != testing, severity != nitpick) that match an
             expected issue / all defect findings. Test-gap findings are reported separately.
Plus tokens, estimated cost and latency per review.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from graphreview.common.config import Settings
from graphreview.common.models import FinalReview, Finding, ReviewJob
from graphreview.eval.cases import EVAL_REPO, Expected, materialize
from graphreview.pipeline import LocalPipeline

__all__ = ["EVAL_REPO", "Expected", "cost_usd", "markdown_table", "materialize", "run_eval", "score", "summarize"]

# $/MTok: input, output, cache read, cache write (5-min)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
}


def score(findings: list[Finding], expected: list[Expected]) -> dict:
    defects = [f for f in findings if f.category.value != "testing" and f.severity.value != "nitpick"]
    matched_expected = sum(1 for x in expected if any(x.matches(f) for f in defects))
    true_findings = sum(1 for f in defects if any(x.matches(f) for x in expected))
    return {"expected": len(expected), "hit": matched_expected, "defect_findings": len(defects),
            "true_findings": true_findings, "test_gap_findings": len(findings) - len(defects)}


def cost_usd(model: str, review: FinalReview, backend: str = "anthropic") -> float:
    if backend != "anthropic":
        return 0.0  # fake backend token counts are estimates, not billable usage
    pin, pout, pread, pwrite = PRICES.get(model, PRICES["claude-opus-5-5"])
    u = review.usage
    return (u.input_tokens * pin + u.output_tokens * pout + u.cache_read_tokens * pread
            + u.cache_write_tokens * pwrite) / 1e6


def summarize(rows: list[dict]) -> dict:
    exp = sum(r["score"]["expected"] for r in rows)
    hit = sum(r["score"]["hit"] for r in rows)
    defects = sum(r["score"]["defect_findings"] for r in rows)
    true_ = sum(r["score"]["true_findings"] for r in rows)
    precision = true_ / defects if defects else 1.0
    recall = hit / exp if exp else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    lat = sorted(r["latency_s"] for r in rows)
    return {
        "cases": len(rows), "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
        "false_positives_on_clean_prs": sum(r["score"]["defect_findings"] for r in rows if r["score"]["expected"] == 0),
        "avg_input_tokens": round(statistics.mean(r["input_tokens"] for r in rows)),
        "avg_output_tokens": round(statistics.mean(r["output_tokens"] for r in rows)),
        "avg_cost_usd": round(statistics.mean(r["cost_usd"] for r in rows), 4),
        "latency_p50_s": round(statistics.median(lat), 1),
        "latency_max_s": round(lat[-1], 1),
    }


async def run_eval(settings: Settings, cases_dir: Path, fixture: Path, modes: list[str], out_dir: Path,
                   only: list[str] | None = None) -> dict:
    cases = [json.loads(p.read_text()) for p in sorted(cases_dir.glob("*.json"))]
    if only:
        cases = [c for c in cases if c["id"] in only]
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {"model": settings.agent_model, "llm_backend": settings.llm_backend,
                    "embedding_backend": settings.embedding_backend, "modes": {}}
    for mode in modes:
        rows = []
        async with LocalPipeline(settings) as pipe:
            idx = await pipe.index(EVAL_REPO, fixture)
            report["index"] = idx.model_dump()
            for case in cases:
                head, files, expected = materialize(case, fixture, work)
                job = ReviewJob(source="local", repo=EVAL_REPO, pr_number=0, title=case["title"],
                                body=case.get("body", ""), files=files, local_path=str(head), context_mode=mode)
                t0 = time.monotonic()
                final = await pipe.review(job)
                s = score(final.findings, expected)
                rows.append({
                    "case": case["id"], "score": s, "latency_s": round(time.monotonic() - t0, 2),
                    "input_tokens": final.usage.input_tokens + final.usage.cache_read_tokens,
                    "output_tokens": final.usage.output_tokens, "cost_usd": round(cost_usd(settings.agent_model, final, settings.llm_backend), 4),
                    "verdict": final.verdict.value, "agents": final.agents,
                    "findings": [f"{f.severity.value} {f.file}:{f.line} [{','.join(f.reported_by or [f.agent])}] {f.title}"
                                 for f in final.findings],
                    "dropped_by_critic": len(final.dropped),
                })
                print(f"  [{mode}] {case['id']:<32} hit {s['hit']}/{s['expected']}  "
                      f"findings {s['true_findings']}/{s['defect_findings']} true  {rows[-1]['latency_s']}s", flush=True)
        report["modes"][mode] = {"summary": summarize(rows), "cases": rows}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"eval-{stamp}.json").write_text(json.dumps(report, indent=2))
    (out_dir / f"eval-{stamp}.md").write_text(markdown_table(report))
    return report


def markdown_table(report: dict) -> str:
    lines = [f"Model: `{report['model']}` · LLM backend: `{report['llm_backend']}` · "
             f"embeddings: `{report['embedding_backend']}`", "",
             "| context mode | precision | recall | F1 | FP on clean PRs | avg input tok | avg output tok | avg $/review | p50 latency |",
             "|---|---|---|---|---|---|---|---|---|"]
    for mode, m in report["modes"].items():
        s = m["summary"]
        lines.append(f"| {mode} | {s['precision']} | {s['recall']} | {s['f1']} | {s['false_positives_on_clean_prs']} | "
                     f"{s['avg_input_tokens']:,} | {s['avg_output_tokens']:,} | {s['avg_cost_usd']} | {s['latency_p50_s']}s |")
    return "\n".join(lines) + "\n"
