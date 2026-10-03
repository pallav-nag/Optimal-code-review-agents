"""Query layer over a Graphify-format code graph (networkx node-link JSON).

Answers the structural questions a reviewer asks about a diff without loading files:
which symbols did these hunks touch, who calls them, what transitively depends on them,
and which tests exercise them. Results are rendered as compact text for the LLM.
"""
from __future__ import annotations

import bisect
import re
from collections import deque
from dataclasses import dataclass
from pathlib import PurePosixPath

import networkx as nx

CALL_RELS = {"calls", "indirect_call", "references", "uses", "uses_static_prop", "references_constant",
             "bound_to", "listened_by"}
DEP_RELS = CALL_RELS | {"imports", "imports_from", "inherits", "extends", "implements", "re_exports"}
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$")


def _line(loc) -> int | None:
    if isinstance(loc, int):
        return loc
    if isinstance(loc, str) and (m := re.match(r"L?(\d+)", loc)):
        return int(m.group(1))
    return None


@dataclass
class Hit:
    node: str
    distance: int
    relation: str
    via: str | None  # neighbour that led here (for explanations)


class CodeGraph:
    def __init__(self, data: dict):
        self.G = nx.MultiDiGraph()
        for n in data.get("nodes", []):
            self.G.add_node(n["id"], **n)
        for e in data.get("links", data.get("edges", [])):
            attrs = {k: v for k, v in e.items() if k not in ("source", "target")}
            self.G.add_edge(e["source"], e["target"], **attrs)
        self._by_file: dict[str, list[tuple[int, str]]] = {}
        self._file_nodes: dict[str, str] = {}
        self._by_label: dict[str, list[str]] = {}
        for nid, d in self.G.nodes(data=True):
            sf = d.get("source_file")
            if not sf:
                continue
            sf = self._norm(sf)
            # Graphify file nodes: label == basename, no (or L1) location
            if d.get("kind") == "file" or (
                d.get("label") == PurePosixPath(sf).name and _line(d.get("source_location")) in (None, 1)
            ):
                self._file_nodes[sf] = nid
                continue
            ln = _line(d.get("source_location"))
            if ln is not None:
                self._by_file.setdefault(sf, []).append((ln, nid))
            label = str(d.get("label", "")).rstrip("()")
            self._by_label.setdefault(label, []).append(nid)
        for v in self._by_file.values():
            v.sort()

    # ── lookups ──────────────────────────────────────────────────────────────
    @staticmethod
    def _norm(path: str) -> str:
        return path.removeprefix("./")

    def resolve_path(self, path: str) -> str | None:
        """Exact match, else unique suffix match (handles absolute paths in graph.json)."""
        path = self._norm(path)
        if path in self._by_file or path in self._file_nodes:
            return path
        cands = {p for p in (*self._by_file, *self._file_nodes) if p.endswith("/" + path) or path.endswith("/" + p)}
        return cands.pop() if len(cands) == 1 else None

    def symbols_at(self, path: str, lines: list[int]) -> list[str]:
        """Innermost symbol enclosing each line (by end_line when known, else nearest preceding def)."""
        p = self.resolve_path(path)
        if not p:
            return []
        entries = self._by_file.get(p, [])
        starts = [s for s, _ in entries]
        out: list[str] = []
        for ln in lines:
            i = bisect.bisect_right(starts, ln) - 1
            best = None
            while i >= 0:
                nid = entries[i][1]
                end = self.G.nodes[nid].get("end_line")
                if end is None or end >= ln:
                    best = nid
                    break
                i -= 1
            if best and best not in out:
                out.append(best)
        if not out and p in self._file_nodes:
            out.append(self._file_nodes[p])
        return out

    def find(self, name: str, limit: int = 5) -> list[str]:
        name = name.strip().rstrip("()")
        if name in self.G:
            return [name]
        short = name.split("::")[-1].split(".")[-1]
        hits = self._by_label.get(name) or self._by_label.get(short) or []
        return hits[:limit]

    def describe(self, nid: str) -> str:
        d = self.G.nodes.get(nid, {})
        loc = _line(d.get("source_location"))
        where = f"{d.get('source_file', '?')}:{loc}" if loc else d.get("source_file", "?")
        kind = d.get("kind") or d.get("file_type", "")
        return f"{d.get('label', nid)} [{kind}] @ {where}"

    def location(self, nid: str) -> tuple[str | None, int | None, int | None]:
        d = self.G.nodes.get(nid, {})
        return d.get("source_file"), _line(d.get("source_location")), d.get("end_line")

    # ── traversals ───────────────────────────────────────────────────────────
    def _bfs(self, seeds: list[str], rels: set[str], reverse: bool, depth: int, limit: int) -> list[Hit]:
        seen = set(seeds)
        out: list[Hit] = []
        q = deque((s, 0) for s in seeds if s in self.G)
        while q and len(out) < limit:
            node, dist = q.popleft()
            if dist >= depth:
                continue
            edges = self.G.in_edges(node, data=True) if reverse else self.G.out_edges(node, data=True)
            for u, v, d in edges:
                nxt = u if reverse else v
                if d.get("relation") not in rels or nxt in seen:
                    continue
                seen.add(nxt)
                out.append(Hit(nxt, dist + 1, d.get("relation", ""), node))
                q.append((nxt, dist + 1))
        return out

    def callers(self, nid: str, depth: int = 1, limit: int = 25) -> list[Hit]:
        return self._bfs([nid], CALL_RELS, reverse=True, depth=depth, limit=limit)

    def callees(self, nid: str, depth: int = 1, limit: int = 25) -> list[Hit]:
        return self._bfs([nid], CALL_RELS, reverse=False, depth=depth, limit=limit)

    def impact(self, seeds: list[str], depth: int = 2, limit: int = 40) -> list[Hit]:
        """Reverse-dependency closure: everything that (transitively) calls/imports/extends the seeds."""
        hits = self._bfs(seeds, DEP_RELS, reverse=True, depth=depth, limit=limit)
        # rank: closer first, then by fan-in (more central symbols matter more)
        return sorted(hits, key=lambda h: (h.distance, -self.G.in_degree(h.node)))

    def tests_for(self, seeds: list[str], depth: int = 3) -> list[str]:
        hits = self._bfs(seeds, DEP_RELS, reverse=True, depth=depth, limit=200)
        return [h.node for h in hits if TEST_PATH.search(str(self.G.nodes[h.node].get("source_file", "")))]

    def is_test(self, nid: str) -> bool:
        return bool(TEST_PATH.search(str(self.G.nodes.get(nid, {}).get("source_file", ""))))

    def stats(self) -> dict:
        rels: dict[str, int] = {}
        for _, _, d in self.G.edges(data=True):
            rels[d.get("relation", "?")] = rels.get(d.get("relation", "?"), 0) + 1
        return {"nodes": self.G.number_of_nodes(), "edges": self.G.number_of_edges(),
                "files": len(self._file_nodes), "relations": rels}

    # ── rendering ────────────────────────────────────────────────────────────
    def render_hits(self, title: str, hits: list[Hit], budget_chars: int = 4000) -> str:
        lines = [title]
        for h in hits:
            via = f" ← {self.G.nodes.get(h.via, {}).get('label', h.via)}" if h.via else ""
            line = f"  [d{h.distance}] {self.describe(h.node)} ({h.relation}{via})"
            if sum(map(len, lines)) + len(line) > budget_chars:
                lines.append(f"  … {len(hits) - len(lines) + 1} more truncated")
                break
            lines.append(line)
        if len(lines) == 1:
            lines.append("  (none found in graph)")
        return "\n".join(lines)
