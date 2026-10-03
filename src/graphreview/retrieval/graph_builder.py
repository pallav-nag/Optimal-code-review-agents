"""Built-in Python code-graph builder.

Emits the same node-link JSON schema as Graphify's `graph.json` (nodes: id/label/file_type/
source_file/source_location; links: source/target/relation/confidence) so `CodeGraph` can
consume either. The indexer prefers `graphify extract` (tree-sitter, 25+ languages) and
falls back to this when Graphify isn't installed or fails — and tests use it directly.

Call resolution is import-aware: `from pkg.mod import fn` binds `fn` to `pkg/mod.py::fn`;
otherwise same-file definitions win, then a global by-name match if it is unambiguous enough.
"""
from __future__ import annotations

import ast
import logging
from collections import defaultdict
from pathlib import Path

log = logging.getLogger(__name__)

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages", "build", "dist",
             ".tox", ".mypy_cache", ".pytest_cache", "graphify-out"}
MAX_GLOBAL_CANDIDATES = 3


def iter_source_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in suffixes and not (set(p.relative_to(root).parts) & SKIP_DIRS):
            out.append(p)
    return out


def _parent(nid: str) -> str:
    """`pkg/a.py::Cls.meth` → `pkg/a.py::Cls`; top-level symbols → their file."""
    f, _, q = nid.partition("::")
    return f"{f}::{q.rsplit('.', 1)[0]}" if "." in q else f


def _module_name(rel: str) -> str:
    return rel[:-3].replace("/", ".").removesuffix(".__init__")


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel: str):
        self.rel = rel
        self.stack: list[tuple[str, str]] = []  # (node_id, kind)
        self.defs: list[dict] = []
        self.contains: list[tuple[str, str, int]] = []
        self.calls: list[tuple[str, str, bool, int]] = []  # caller, name, is_self_attr, line
        self.bases: list[tuple[str, str, int]] = []
        self.imports: dict[str, str] = {}  # local name → dotted module (or module.attr)

    @property
    def scope(self) -> str:
        return self.stack[-1][0] if self.stack else self.rel

    def _qual(self, name: str) -> str:
        quals = [nid.split("::", 1)[1] for nid, _ in self.stack]
        return ".".join([*quals[-1:], name]) if quals else name

    def _def(self, node, kind: str):
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        nid = f"{self.rel}::{self._qual(node.name)}"
        if self.stack and self.stack[-1][1] == "class" and kind == "function":
            kind = "method"
        self.defs.append({
            "id": nid, "label": node.name, "kind": kind, "file_type": "code", "source_file": self.rel,
            "source_location": f"L{start}", "end_line": node.end_lineno,
        })
        self.contains.append((self.scope, nid, start))
        return nid, kind

    def visit_ClassDef(self, node):
        nid, _ = self._def(node, "class")
        for b in node.bases:
            name = b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else None
            if name:
                self.bases.append((nid, name, node.lineno))
        self.stack.append((nid, "class"))
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        nid, kind = self._def(node, "function")
        self.stack.append((nid, kind))
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Name):
            self.calls.append((self.scope, f.id, False, node.lineno))
        elif isinstance(f, ast.Attribute):
            is_self = isinstance(f.value, ast.Name) and f.value.id in ("self", "cls")
            self.calls.append((self.scope, f.attr, is_self, node.lineno))
        self.generic_visit(node)

    def visit_Import(self, node):
        for a in node.names:
            self.imports[(a.asname or a.name).split(".")[0]] = a.name

    def visit_ImportFrom(self, node):
        if node.level:  # relative import → resolve against this file's package
            pkg = _module_name(self.rel).split(".")
            base = pkg[: len(pkg) - node.level] if not self.rel.endswith("__init__.py") else pkg[: len(pkg) - node.level + 1]
            mod = ".".join([*base, node.module] if node.module else base)
        else:
            mod = node.module or ""
        for a in node.names:
            self.imports[a.asname or a.name] = f"{mod}.{a.name}" if mod else a.name


def build_python_graph(root: str | Path) -> dict:
    root = Path(root)
    nodes: list[dict] = []
    links: list[dict] = []
    modules: dict[str, str] = {}
    visitors: list[_Visitor] = []

    for path in iter_source_files(root, (".py",)):
        rel = path.relative_to(root).as_posix()
        modules[_module_name(rel)] = rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=rel)
        except SyntaxError as e:
            log.debug("skip %s: %s", rel, e)
            continue
        v = _Visitor(rel)
        v.visit(tree)
        visitors.append(v)
        nodes.append({"id": rel, "label": path.name, "kind": "file", "file_type": "code",
                      "source_file": rel, "source_location": "L1"})
        nodes.extend(v.defs)

    by_name: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        if n["kind"] != "file":
            by_name[n["label"]].append(n["id"])

    def edge(src, dst, rel, line, conf="EXTRACTED", source_file=""):
        links.append({"source": src, "target": dst, "relation": rel, "confidence": conf,
                      "source_file": source_file, "source_location": f"L{line}", "weight": 1.0})

    for v in visitors:
        for parent, child, line in v.contains:
            edge(parent, child, "contains", line, source_file=v.rel)

        # file → file import edges
        seen_imports = set()
        for target in v.imports.values():
            parts = target.split(".")
            for i in range(len(parts), 0, -1):
                mod = ".".join(parts[:i])
                if mod in modules and modules[mod] != v.rel and modules[mod] not in seen_imports:
                    seen_imports.add(modules[mod])
                    edge(v.rel, modules[mod], "imports", 1, source_file=v.rel)
                    break

        def resolve(name: str, is_self: bool, caller: str, _v=v) -> tuple[list[str], str]:
            # 1. explicitly imported symbol
            if not is_self and name in _v.imports:
                target = _v.imports[name]
                mod, _, sym = target.rpartition(".")
                if mod in modules:
                    cands = [c for c in by_name.get(sym, []) if c.startswith(modules[mod] + "::")]
                    if cands:
                        return cands[:1], "EXTRACTED"
            cands = by_name.get(name, [])
            # 2. same class (self.method()) / same file
            if is_self:
                cls = _parent(caller)
                same_cls = [c for c in cands if _parent(c) == cls]
                if same_cls:
                    return same_cls[:1], "EXTRACTED"
            same_file = [c for c in cands if c.startswith(_v.rel + "::")]
            if same_file:
                return same_file[:1], "EXTRACTED"
            # 3. global by-name, only if not too ambiguous
            if 0 < len(cands) <= MAX_GLOBAL_CANDIDATES:
                return cands, "EXTRACTED" if len(cands) == 1 else "INFERRED"
            return [], ""

        seen_calls = set()
        for caller, name, is_self, line in v.calls:
            targets, conf = resolve(name, is_self, caller)
            for t in targets:
                if t != caller and (caller, t) not in seen_calls:
                    seen_calls.add((caller, t))
                    edge(caller, t, "calls", line, conf, v.rel)
        for cls, base, line in v.bases:
            targets, conf = resolve(base, False, cls)
            for t in targets:
                edge(cls, t, "inherits", line, conf, v.rel)

    return {"directed": True, "multigraph": True, "graph": {"builder": "graphreview-ast"},
            "nodes": nodes, "links": links}
