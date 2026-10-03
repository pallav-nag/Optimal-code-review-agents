"""Unified-diff parsing: split a git diff per file and map hunks to new-side line numbers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FilePatch:
    path: str
    old_path: str | None
    status: str  # added | modified | removed | renamed
    patch: str  # hunks only (starting at the first @@)
    additions: int = 0
    deletions: int = 0


@dataclass
class HunkLines:
    added: list[int] = field(default_factory=list)  # new-side line numbers of '+' lines
    context: list[int] = field(default_factory=list)  # new-side line numbers of ' ' lines


def parse_patch_lines(patch: str) -> HunkLines:
    """Return new-side line numbers for added and context lines of a single-file patch."""
    out = HunkLines()
    new_line = 0
    for raw in patch.splitlines():
        m = HUNK_RE.match(raw)
        if m:
            new_line = int(m.group(3))
            continue
        if new_line == 0 or raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            out.added.append(new_line)
            new_line += 1
        elif raw.startswith("-"):
            continue
        else:
            out.context.append(new_line)
            new_line += 1
    return out


def annotate_patch(patch: str) -> str:
    """Prefix each new-side line with its line number so the model can cite exact lines.

    Removed lines get a blank gutter — they no longer exist in the new file.
    """
    lines: list[str] = []
    new_line = 0
    for raw in patch.splitlines():
        m = HUNK_RE.match(raw)
        if m:
            new_line = int(m.group(3))
            lines.append(raw)
            continue
        if new_line == 0 or raw.startswith("\\"):
            continue
        if raw.startswith("-"):
            lines.append(f"{'':>5} {raw}")
        else:
            lines.append(f"{new_line:>5} {raw}")
            new_line += 1
    return "\n".join(lines)


def split_git_diff(diff_text: str) -> list[FilePatch]:
    """Split `git diff` output into per-file patches."""
    files: list[FilePatch] = []
    chunks = re.split(r"(?m)^diff --git ", diff_text)
    for chunk in chunks:
        if not chunk.strip():
            continue
        header, _, _ = chunk.partition("\n@@")
        old_path = new_path = None
        status = "modified"
        for line in header.splitlines():
            if line.startswith("--- "):
                p = line[4:].strip()
                old_path = None if p == "/dev/null" else p.removeprefix("a/")
            elif line.startswith("+++ "):
                p = line[4:].strip()
                new_path = None if p == "/dev/null" else p.removeprefix("b/")
            elif line.startswith("new file mode"):
                status = "added"
            elif line.startswith("deleted file mode"):
                status = "removed"
            elif line.startswith("rename from"):
                status = "renamed"
        if new_path is None and old_path is None:
            # header-only chunk (binary or mode change) — take path from the `a/x b/x` line
            first = chunk.splitlines()[0]
            m = re.match(r"a/(\S+) b/(\S+)", first)
            if not m:
                continue
            old_path, new_path = m.group(1), m.group(2)
        if new_path is None:
            status = "removed"
        idx = chunk.find("\n@@")
        patch = chunk[idx + 1 :] if idx >= 0 else ""
        adds = sum(1 for ln in patch.splitlines() if ln.startswith("+"))
        dels = sum(1 for ln in patch.splitlines() if ln.startswith("-"))
        files.append(
            FilePatch(
                path=new_path or old_path or "",
                old_path=old_path if old_path != new_path else None,
                status=status,
                patch=patch.rstrip("\n"),
                additions=adds,
                deletions=dels,
            )
        )
    return files
