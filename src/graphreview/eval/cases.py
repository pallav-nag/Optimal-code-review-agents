"""Seeded-bug cases: apply find/replace edits to a copy of the fixture repo and compute the PR diff.

Kept free of heavy imports so the gateway (demo mode) and the CLI client can use it.
"""
from __future__ import annotations

import difflib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from graphreview.common.models import ChangedFile, Finding

TOLERANCE = 3
EVAL_REPO = "eval/shopapp"


@dataclass
class Expected:
    file: str
    start: int
    end: int
    category: str | None = None
    alternatives: list[tuple[str, int, int]] = field(default_factory=list)

    def matches(self, f: Finding) -> bool:
        for path, s, e in [(self.file, self.start, self.end), *self.alternatives]:
            if f.file == path and f.line is not None and s - TOLERANCE <= f.line <= e + TOLERANCE:
                return True
        return False


def _anchor_range(text: str, anchor: str) -> tuple[int, int]:
    idx = text.find(anchor)
    if idx < 0:
        raise ValueError(f"anchor not found: {anchor[:60]!r}")
    start = text.count("\n", 0, idx) + 1
    return start, start + anchor.strip("\n").count("\n")


def materialize(case: dict, fixture: Path, workdir: Path) -> tuple[Path, list[ChangedFile], list[Expected]]:
    head = workdir / case["id"]
    if head.exists():
        shutil.rmtree(head)
    shutil.copytree(fixture, head)
    files: list[ChangedFile] = []
    by_file: dict[str, list[dict]] = {}
    for e in case["edits"]:
        by_file.setdefault(e["file"], []).append(e)
    for path, edits in by_file.items():
        p = head / path
        before = p.read_text()
        after = before
        for e in edits:
            if after.count(e["find"]) != 1:
                raise ValueError(f"{case['id']}: `find` must occur exactly once in {path}")
            after = after.replace(e["find"], e["replace"])
        p.write_text(after)
        diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}",
                                         n=3, lineterm=""))
        patch = "\n".join(diff[2:])
        files.append(ChangedFile(path=path, status="modified", patch=patch,
                                 additions=sum(1 for ln in diff[2:] if ln.startswith("+")),
                                 deletions=sum(1 for ln in diff[2:] if ln.startswith("-"))))
    expected = []
    for x in case.get("expected", []):
        s, e = _anchor_range((head / x["file"]).read_text(), x["anchor"])
        alts = []
        for a in x.get("alternatives", []):
            ast_, ae = _anchor_range((head / a["file"]).read_text(), a["anchor"])
            alts.append((a["file"], ast_, ae))
        expected.append(Expected(x["file"], s, e, x.get("category"), alts))
    return head, files, expected
