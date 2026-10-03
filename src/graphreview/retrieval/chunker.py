"""Syntax-aware chunking for RAG.

Python files are split on AST boundaries (functions, classes; large classes per method;
decorators kept with their def) and the gaps between definitions become `module` chunks,
so the chunks of a file tile it completely — `read_snippet` can rebuild any line range from
the index. Markdown/RST is split on headings (kind=`doc`, used for repo guideline retrieval).
Other languages fall back to overlapping line windows.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

CODE_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".java": "java", ".kt": "kotlin", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".cs": "csharp", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".scala": "scala", ".swift": "swift",
    ".sql": "sql", ".sh": "bash", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
}
DOC_EXT = {".md": "markdown", ".rst": "rst", ".txt": "text"}
MAX_FILE_BYTES = 400_000


@dataclass
class Chunk:
    path: str
    start_line: int
    end_line: int
    text: str
    kind: str = "code"  # code | doc
    symbol: str | None = None
    lang: str = ""

    def embed_text(self) -> str:
        head = f"{self.path}" + (f" :: {self.symbol}" if self.symbol else "")
        return f"{head}\n{self.text}"


def _windows(path, lines, start, end, kind, lang, symbol=None, size=80, overlap=10) -> list[Chunk]:
    out = []
    s = start
    while s <= end:
        e = min(s + size - 1, end)
        out.append(Chunk(path, s, e, "\n".join(lines[s - 1 : e]), kind, symbol, lang))
        if e == end:
            break
        s = e + 1 - overlap
    return out


def _python_spans(tree: ast.Module, max_lines: int) -> list[tuple[int, int, str | None]]:
    spans: list[tuple[int, int, str | None]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        end = node.end_lineno or node.lineno
        if isinstance(node, ast.ClassDef) and end - start + 1 > max_lines:
            methods = [n for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
            cursor = start
            for m in methods:
                m_start = min([m.lineno] + [d.lineno for d in m.decorator_list])
                if m_start > cursor:
                    spans.append((cursor, m_start - 1, node.name))
                spans.append((m_start, m.end_lineno or m_start, f"{node.name}.{m.name}"))
                cursor = (m.end_lineno or m_start) + 1
            if cursor <= end:
                spans.append((cursor, end, node.name))
        else:
            spans.append((start, end, node.name))
    return spans


def chunk_python(path: str, text: str, max_lines: int = 80) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _windows(path, lines, 1, len(lines), "code", "python")
    chunks: list[Chunk] = []
    cursor = 1
    for s, e, sym in _python_spans(tree, max_lines):
        if s > cursor:  # module-level gap (imports, constants, scripts)
            chunks += _windows(path, lines, cursor, s - 1, "code", "python", "<module>", size=max_lines, overlap=0)
        if e - s + 1 > max_lines * 1.5:
            chunks += _windows(path, lines, s, e, "code", "python", sym, size=max_lines, overlap=0)
        else:
            chunks.append(Chunk(path, s, e, "\n".join(lines[s - 1 : e]), "code", sym, "python"))
        cursor = e + 1
    if cursor <= len(lines):
        chunks += _windows(path, lines, cursor, len(lines), "code", "python", "<module>", size=max_lines, overlap=0)
    return [c for c in chunks if c.text.strip()]


def chunk_markdown(path: str, text: str, lang: str, max_lines: int = 60) -> list[Chunk]:
    lines = text.splitlines()
    heads = [i + 1 for i, ln in enumerate(lines) if re.match(r"^#{1,4} ", ln)] or [1]
    if heads[0] != 1:
        heads.insert(0, 1)
    out: list[Chunk] = []
    for i, s in enumerate(heads):
        e = heads[i + 1] - 1 if i + 1 < len(heads) else len(lines)
        title = lines[s - 1].lstrip("# ").strip() if lines and lines[s - 1].startswith("#") else None
        out += _windows(path, lines, s, e, "doc", lang, title, size=max_lines, overlap=5)
    return [c for c in out if c.text.strip()]


def chunk_file(path: str, text: str) -> list[Chunk]:
    if len(text.encode("utf-8", errors="ignore")) > MAX_FILE_BYTES or "\x00" in text[:4096]:
        return []
    suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if suffix == ".py":
        return chunk_python(path, text)
    if suffix in DOC_EXT:
        return chunk_markdown(path, text, DOC_EXT[suffix])
    if suffix in CODE_EXT:
        lines = text.splitlines()
        return [c for c in _windows(path, lines, 1, len(lines), "code", CODE_EXT[suffix]) if c.text.strip()]
    return []
