"""Architecture diagrams as code.

Every figure is generated here so coordinates stay on a grid and the docs never drift from one source.

    python docs/diagrams/build.py        (or: make docs)

Writes:
  docs/img/arch/*.svg        standalone figures (light + dark), embedded in README.md and docs/ARCHITECTURE.md
  docs/architecture.html     the interactive guide (clickable system map, component notes)
"""
import html
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent
OUT_HTML = DOCS / "architecture.html"
OUT_SVG = DOCS / "img" / "arch"


def esc(s):
    return html.escape(str(s), quote=True)


def T(x, y, s, cls, anchor="middle"):
    return f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{esc(s)}</text>'


def defs(p):
    kinds = {"i": "m-ink", "k": "m-kafka", "l": "m-llm", "b": "m-bad", "s": "m-store", "o": "m-ok"}
    ms = "".join(
        f'<marker id="{p}{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="{c}"/></marker>'
        for k, c in kinds.items())
    return f"<defs>{ms}</defs>"


def A(p, d, cls="", mk="i", start=False):
    s = f' marker-start="url(#{p}{mk})"' if start else ""
    return f'<path d="{d}" class="e {cls}" marker-end="url(#{p}{mk})"{s}/>'


def box(x, y, w, h, lines, cls="d-box", node=None):
    """lines: [(text, class)], first line is the title. Vertically centred."""
    cx = x + w / 2
    n = len(lines)
    total = 14 + 15 * (n - 1)
    y0 = y + (h - total) / 2 + 12
    out = []
    if node:
        out.append(f'<g class="node" data-node="{node}" tabindex="0" role="button" aria-label="{esc(lines[0][0])}">')
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" class="{cls}"/>')
    for i, (t, c) in enumerate(lines):
        yy = y0 + (0 if i == 0 else 16 + 15 * (i - 1))
        out.append(T(round(cx, 1), round(yy, 1), t, c))
    if node:
        out.append("</g>")
    return "".join(out)


def svg(vb_w, vb_h, label, body, min_w=640):
    return (f'<div class="scroll"><svg viewBox="0 0 {vb_w} {vb_h}" role="img" aria-label="{esc(label)}" '
            f'style="min-width:{min_w}px">{body}</svg></div>')


# ── Fig 1: system map ────────────────────────────────────────────────────────
def fig_system():
    p = "f1"
    b = [defs(p)]
    X, W = 120, 260
    CX = X + W / 2
    b.append(box(X, 16, W, 46, [("GitHub", "t-title"), ("pull_request · push webhooks", "t-sub")], node="github"))
    b.append(A(p, f"M{CX} 62V106"))
    b.append(T(CX + 10, 89, "POST /webhook · HMAC checked", "t-note", "start"))
    b.append(box(X, 108, W, 72, [("gateway", "t-title"), ("verifies signature · fetches PR files", "t-sub"),
                                  ("dedupes by head SHA · serves dashboard", "t-sub"),
                                  ("Redis: job, status=queued", "t-store")], node="gateway"))
    b.append(A(p, f"M{CX} 180V238", "e-kafka", "k"))
    b.append(T(CX + 10, 206, "pr.review.requested", "t-kafka", "start"))
    b.append(T(CX + 10, 221, "key = job_id · 6 partitions", "t-note", "start"))
    # agents group
    b.append('<g class="node" data-node="agents" tabindex="0" role="button" aria-label="agents">')
    b.append(f'<rect x="{X}" y="240" width="{W}" height="172" rx="9" class="d-group"/>')
    b.append(T(CX, 260, "4 agents · one consumer group each", "t-groupt"))
    b.append("</g>")
    iw = (W - 28) / 2
    cols = [X + 10, X + 18 + iw]
    b.append(box(cols[0], 272, iw, 62, [("static", "t-title"), ("ruff · added lines", "t-sub")], node="static"))
    b.append(box(cols[1], 272, iw, 62, [("reviewer", "t-title"), ("correctness", "t-sub")], "d-llm",
                 node="reviewer"))
    b.append(box(cols[0], 342, iw, 62, [("security", "t-title"), ("injection · authz", "t-sub")], "d-llm",
                 node="security"))
    b.append(box(cols[1], 342, iw, 62, [("tests", "t-title"), ("coverage gaps", "t-sub")], "d-llm",
                 node="tests"))
    b.append(A(p, f"M{CX} 412V468", "e-kafka", "k"))
    b.append(T(CX + 10, 438, "review.agent.results", "t-kafka", "start"))
    b.append(T(CX + 10, 453, "one result per agent", "t-note", "start"))
    b.append(box(X, 470, W, 72, [("aggregator", "t-title"), ("joins results by job_id", "t-sub"),
                                  ("merges duplicate findings", "t-sub"),
                                  ("Redis: partial results, deadlines", "t-store")], node="aggregator"))
    b.append(A(p, f"M{CX} 542V598", "e-kafka", "k"))
    b.append(T(CX + 10, 574, "review.aggregated", "t-kafka", "start"))
    b.append(box(X, 600, W, 72, [("critic", "t-title"), ("Claude as judge", "t-sub"),
                                  ("drops false positives", "t-sub"), ("writes summary + verdict", "t-sub")],
                 "d-llm", node="critic"))
    b.append(A(p, f"M{CX} 672V728", "e-kafka", "k"))
    b.append(T(CX + 10, 704, "review.completed", "t-kafka", "start"))
    b.append(box(X, 730, W, 72, [("publisher", "t-title"), ("one GitHub review", "t-sub"),
                                  ("inline comments on diff lines", "t-sub"),
                                  ("Redis: review, status=completed", "t-store")], node="publisher"))
    b.append(A(p, f"M{CX} 802V848"))
    b.append(T(CX + 10, 829, "POST /pulls/{n}/reviews", "t-note", "start"))
    b.append(box(X, 850, W, 40, [("GitHub PR · review posted", "t-title")], node="github"))

    # right column
    RX, RW = 580, 320
    # legend
    lg = [("e e-kafka", "k", "Kafka topic"), ("e e-tool", "i", "HTTP call to the context service"),
          ("e e-llm", "l", "call to the Claude API")]
    for i, (cls, mk, lab) in enumerate(lg):
        y = 26 + i * 18
        b.append(f'<path d="M{RX} {y}H{RX + 34}" class="{cls}" marker-end="url(#{p}{mk})"/>')
        b.append(T(RX + 44, y + 4, lab, "t-note", "start"))
    b.append(f'<rect x="{RX}" y="78" width="16" height="12" rx="3" class="d-llm"/>')
    b.append(T(RX + 22, 88, "uses Claude", "t-note", "start"))
    b.append(f'<rect x="{RX + 110}" y="78" width="16" height="12" rx="3" class="d-store"/>')
    b.append(T(RX + 132, 88, "storage", "t-note", "start"))

    b.append(A(p, f"M{X + W} 144H{RX - 2}", "e-kafka", "k"))
    b.append(T((X + W + RX) / 2, 136, "repo.index.requested", "t-kafka"))
    b.append(T((X + W + RX) / 2, 160, "push to default branch", "t-note"))
    b.append(box(RX, 108, RW, 72, [("indexer", "t-title"), ("clones the repo · builds the code graph", "t-sub"),
                                    ("chunks + embeds code and docs", "t-sub")], node="indexer"))
    b.append(box(RX, 240, 150, 86, [("Redis", "t-title"), ("code graph (gzip)", "t-sub"),
                                     ("jobs · state · reviews", "t-sub")], "d-store", node="redis"))
    b.append(box(RX + 170, 240, 150, 86, [("Qdrant", "t-title"), ("code + doc chunks", "t-sub"),
                                           ("dense + sparse vectors", "t-sub")], "d-store", node="qdrant"))
    b.append(A(p, f"M{RX + 75} 180V238", "e-store", "s"))
    b.append(T(RX + 83, 214, "graph.json", "t-note", "start"))
    b.append(A(p, f"M{RX + 245} 180V238", "e-store", "s"))
    b.append(T(RX + 253, 214, "vectors", "t-note", "start"))
    b.append(box(RX, 440, RW, 72, [("context service", "t-title"), ("GraphRAG API (HTTP) + MCP server", "t-sub"),
                                    ("impact · callers · search · snippets", "t-sub")], node="context"))
    b.append(A(p, f"M{RX + 75} 440V328", "e-store", "s"))
    b.append(T(RX + 83, 392, "reads graph", "t-note", "start"))
    b.append(A(p, f"M{RX + 245} 440V328", "e-store", "s"))
    b.append(T(RX + 253, 392, "hybrid search", "t-note", "start"))
    b.append(A(p, f"M{X + W} 326H480V466H{RX - 2}", "e-tool"))
    b.append(T(488, 404, "tool calls", "t-note", "start"))
    b.append(A(p, f"M{X + W} 624H520V496H{RX - 2}", "e-tool"))
    b.append(T(528, 566, "pack_context", "t-note", "start"))
    # Claude
    b.append(box(8, 430, 96, 54, [("Claude API", "t-title"), ("Messages", "t-sub")], "d-llm", node="claude"))
    b.append(A(p, f"M{X} 380H56V428", "e-llm", "l"))
    b.append(A(p, f"M{X} 648H56V486", "e-llm", "l"))
    return svg(920, 900, "System map: GitHub webhooks enter the gateway, a job flows through Kafka topics to four "
               "agents, the aggregator, the critic and the publisher, which posts the review back to GitHub. The "
               "indexer fills Redis and Qdrant; agents query them through the context service.", "".join(b), 700)


# ── Fig 2: Kafka consumer groups ─────────────────────────────────────────────
def fig_kafka():
    p = "f2"
    b = [defs(p)]
    ys = [86 + i * 36 for i in range(6)]  # partition cells, h=30
    # topic
    b.append(f'<rect x="330" y="30" width="260" height="284" rx="9" class="d-box"/>')
    b.append(T(460, 52, "pr.review.requested", "t-kafka"))
    b.append(T(460, 70, "6 partitions · hash(job_id) → partition", "t-note"))
    for i, y in enumerate(ys):
        b.append(f'<rect x="350" y="{y}" width="220" height="30" rx="5" class="d-cell"/>')
        b.append(T(362, y + 19, f"partition {i}", "t-sub", "start"))
        for j in range(4 - (i % 2)):
            b.append(f'<rect x="{462 + j * 18}" y="{y + 9}" width="12" height="12" rx="2" class="d-msg"/>')
    # security group (left)
    b.append(T(130, 52, "group agent-security", "t-groupt"))
    b.append(T(130, 70, "1 replica reads all 6", "t-note"))
    b.append(f'<rect x="30" y="86" width="200" height="210" rx="8" class="d-llm"/>')
    b.append(T(130, 180, "replica 1", "t-title"))
    b.append(T(130, 198, "owns partitions 0–5", "t-sub"))
    b.append(T(130, 214, "own committed offsets", "t-note"))
    for y in ys:
        b.append(A(p, f"M350 {y + 15}H232", "e-kafka", "k"))
    # reviewer group (right)
    b.append(T(800, 52, "group agent-reviewer", "t-groupt"))
    b.append(T(800, 70, "3 replicas split the 6", "t-note"))
    for r in range(3):
        y = ys[r * 2]
        b.append(f'<rect x="690" y="{y}" width="220" height="66" rx="8" class="d-llm"/>')
        b.append(T(800, y + 28, f"replica {r + 1}", "t-title"))
        b.append(T(800, y + 46, f"owns partitions {r * 2} + {r * 2 + 1}", "t-sub"))
        for k in (0, 1):
            yy = ys[r * 2 + k] + 15
            b.append(A(p, f"M570 {yy}H688", "e-kafka", "k"))
    return svg(940, 330, "One Kafka topic with six partitions. The security group has one replica reading all six "
               "partitions; the reviewer group has three replicas, each owning two partitions.", "".join(b), 680)


# ── Fig 3: sequence of one review ────────────────────────────────────────────
def fig_sequence():
    p = "f3"
    lanes = ["GitHub", "gateway", "Kafka", "agents ×4", "context", "Claude", "aggregator", "critic", "publisher",
             "Redis"]
    LX = {n: 44 + i * 88 for i, n in enumerate(lanes)}
    kinds = {"Kafka": "d-cell", "Claude": "d-llm", "agents ×4": "d-llm", "critic": "d-llm", "Redis": "d-store"}
    rows = [
        ("GitHub", "gateway", "PR opened (webhook)", ""),
        ("gateway", "GitHub", "GET /pulls/{n}/files", "ret"),
        ("gateway", "Redis", "job:{id} · status", "store", "queued"),
        ("gateway", "Kafka", "pr.review.requested", "kafka"),
        ("Kafka", "agents ×4", "deliver to 4 groups", "kafka"),
        ("agents ×4", "context", "impact(diff)", "tool"),
        ("context", "agents ×4", "impact report", "ret"),
        ("agents ×4", "Claude", "prompt + tools + schema", "llm"),
        ("Claude", "agents ×4", "tool_use", "ret"),
        ("agents ×4", "context", "more tool calls", "tool"),
        ("Claude", "agents ×4", "end_turn → JSON findings", "ret"),
        ("agents ×4", "Kafka", "review.agent.results", "kafka"),
        ("Kafka", "aggregator", "4 results, same partition", "kafka"),
        ("aggregator", "Redis", "progress per agent", "store", "agents"),
        ("aggregator", "Kafka", "review.aggregated (4/4 in)", "kafka"),
        ("aggregator", "Redis", "", "store", "judging"),
        ("Kafka", "critic", "deliver", "kafka"),
        ("critic", "context", "pack_context", "tool"),
        ("critic", "Claude", "judge findings", "llm"),
        ("Claude", "critic", "keep / drop · verdict", "ret"),
        ("critic", "Kafka", "review.completed", "kafka"),
        ("Kafka", "publisher", "deliver", "kafka"),
        ("publisher", "Redis", "final review", "store", "completed"),
        ("publisher", "GitHub", "POST review + inline comments", ""),
    ]
    top, step = 76, 28
    H = top + step * len(rows) + 24
    b = [defs(p)]
    for n, x in LX.items():
        b.append(f'<line x1="{x}" y1="44" x2="{x}" y2="{H - 10}" class="lifeline"/>')
        b.append(f'<rect x="{x - 42}" y="10" width="84" height="32" rx="6" class="{kinds.get(n, "d-box")}"/>')
        b.append(T(x, 31, n, "t-lane"))
    # tool-loop bracket on the agents lane
    y1, y2 = top + step * 7 - 10, top + step * 10 + 6
    ax = LX["agents ×4"]
    b.append(f'<rect x="{ax - 12}" y="{y1}" width="24" height="{y2 - y1}" rx="4" class="d-loop"/>')
    b.append(T(ax - 18, (y1 + y2) / 2 - 3, "tool loop", "t-note", "end"))
    b.append(T(ax - 18, (y1 + y2) / 2 + 11, "≤ 8 turns", "t-note", "end"))
    style = {"": ("", "i"), "ret": ("e-ret", "i"), "store": ("e-store", "s"), "kafka": ("e-kafka", "k"),
             "tool": ("e-tool", "i"), "llm": ("e-llm", "l")}
    for i, row in enumerate(rows):
        frm, to, lab, kind = row[:4]
        y = top + step * i
        x1, x2 = LX[frm], LX[to]
        d = 1 if x2 > x1 else -1
        cls, mk = style[kind]
        b.append(A(p, f"M{x1 + 4 * d} {y}H{x2 - 6 * d}", cls, mk))
        if lab:
            b.append(T((x1 + x2) / 2, y - 6, lab, "t-seq" + (" t-kafka-s" if kind == "kafka" else "")))
        if len(row) > 4:
            b.append(f'<rect x="{LX["Redis"] + 8}" y="{y - 9}" width="70" height="18" rx="9" class="d-pill"/>')
            b.append(T(LX["Redis"] + 43, y + 4, row[4], "t-pill"))
    return svg(LX["Redis"] + 84, H, "Sequence of one review from webhook to posted review, with the status values "
               "the dashboard shows: queued, agents, judging, completed.", "".join(b), 820)


# ── Fig 4: agent tool loop ───────────────────────────────────────────────────
def fig_agent_loop():
    p = "f4"
    b = [defs(p)]
    b.append(box(20, 110, 180, 90, [("Build prompt", "t-title"), ("PR title + description", "t-sub"),
                                     ("diff with line numbers", "t-sub"), ("graph impact report", "t-sub")]))
    b.append(A(p, "M200 155H248"))
    b.append(box(250, 110, 180, 90, [("Call Claude", "t-title"), ("role's system prompt", "t-sub"),
                                      ("5 strict tools + JSON schema", "t-sub"), ("effort · prompt cache", "t-sub")],
                 "d-llm"))
    b.append(A(p, "M430 155H488", "e-llm", "l"))
    b.append('<path d="M490 155L550 110L610 155L550 200Z" class="d-box"/>')
    b.append(T(550, 152, "stop_reason", "t-title"))
    b.append(T(550, 168, "?", "t-sub"))
    b.append(A(p, "M610 155H668", "e-ok", "o"))
    b.append(T(639, 146, "end_turn", "t-note"))
    b.append(box(670, 110, 132, 90, [("Parse findings", "t-title"), ("schema-valid JSON", "t-sub"),
                                      ("→ Finding[]", "t-sub")]))
    b.append(A(p, "M802 155H823"))
    b.append(box(825, 110, 120, 90, [("AgentResult", "t-title"), ("findings · usage", "t-sub"),
                                      ("tool counts · time", "t-sub"), ("→ Kafka", "t-sub")]))
    b.append(A(p, "M550 200V278"))
    b.append(T(560, 244, "tool_use", "t-note", "start"))
    b.append(box(460, 280, 180, 90, [("Run tool calls", "t-title"), ("all at once (asyncio.gather)", "t-sub"),
                                      ("→ context service", "t-sub"), ("errors become is_error", "t-sub")]))
    b.append(A(p, "M460 325H432"))
    b.append(box(250, 280, 180, 90, [("Append to history", "t-title"), ("assistant turn, verbatim", "t-sub"),
                                      ("+ every tool_result", "t-sub"), ("in one user message", "t-sub")]))
    b.append(A(p, "M340 280V202"))
    b.append(T(350, 244, "next turn", "t-note", "start"))
    b.append(T(350, 259, "(last turn: tools off)", "t-note", "start"))
    b.append(A(p, "M550 110V44H668", "e-bad", "b"))
    b.append(T(560, 98, "refusal · max_tokens · API error", "t-note", "start"))
    b.append(box(670, 14, 275, 60, [("AgentResult with error", "t-title"),
                                     ("aggregator records it · review still completes", "t-sub")], "d-bad"))
    return svg(960, 390, "Agent loop: build the prompt, call Claude, then either run tools and loop, parse the final "
               "JSON findings, or record an error result.", "".join(b), 760)


# ── Fig 5: indexing ──────────────────────────────────────────────────────────
def fig_index():
    p = "f5"
    b = [defs(p)]
    b.append(box(20, 70, 150, 90, [("repo checkout", "t-title"), ("default branch", "t-sub"),
                                    ("git clone --depth 1", "t-sub")]))
    b.append(A(p, "M170 100H196V50H228"))
    b.append(A(p, "M170 130H196V180H228"))
    b.append(box(230, 14, 210, 72, [("graph builder", "t-title"), ("graphify extract --code-only", "t-sub"),
                                     ("fallback: Python AST builder", "t-sub")]))
    b.append(A(p, "M440 50H478"))
    b.append(box(480, 14, 190, 72, [("graph.json", "t-title"), ("nodes: functions, classes, files", "t-sub"),
                                     ("edges: calls, imports, inherits", "t-sub")]))
    b.append(A(p, "M670 50H708", "e-store", "s"))
    b.append(box(710, 14, 230, 72, [("Redis", "t-title"), ("graph:{repo} (gzip) + version", "t-sub"),
                                     ("sample app: 60 nodes · 147 edges", "t-sub")], "d-store"))
    b.append(box(230, 144, 210, 72, [("chunker", "t-title"), ("functions / classes / module gaps", "t-sub"),
                                      ("markdown split by heading", "t-sub")]))
    b.append(A(p, "M440 180H478"))
    b.append(box(480, 144, 190, 72, [("embedder", "t-title"), ("dense: BGE-small, 384-d", "t-sub"),
                                      ("sparse: BM25 terms", "t-sub")]))
    b.append(A(p, "M670 180H708", "e-store", "s"))
    b.append(box(710, 144, 230, 72, [("Qdrant", "t-title"), ("one point per chunk", "t-sub"),
                                      ("sample app: 54 chunks", "t-sub")], "d-store"))
    return svg(960, 230, "Indexing: one checkout feeds two lanes. The graph lane writes a code graph to Redis; the "
               "chunk lane embeds code and docs into Qdrant.", "".join(b), 760)


# ── Fig 6: code graph neighbourhood ──────────────────────────────────────────
def fig_graph():
    import math
    p = "f6"
    b = [defs(p)]
    cx, cy = 300, 232
    b.append(f'<circle cx="{cx}" cy="{cy}" r="105" class="ring"/>')
    b.append(f'<circle cx="{cx}" cy="{cy}" r="185" class="ring"/>')
    b.append(T(cx, cy - 111, "1 hop", "t-note"))
    b.append(T(cx, cy - 191, "2 hops", "t-note"))
    # name: (x, y, kind, sub, label placement)
    nodes = {
        "compute_tax": (cx, cy, "hit", "pricing.py:19 · edited", "below"),
        "order_total": (cx + 91, cy - 52, "d1", "pricing.py:24", "right"),
        "test_tax_rounds_half_up": (cx - 91, cy + 52, "t", "tests/test_pricing.py:15", "below"),
        "checkout": (cx + 140, cy - 121, "d2", "api.py:27", "right"),
        "test_order_total": (cx + 172, cy + 68, "t", "tests/test_pricing.py:19", "below"),
        "apply_discount": (cx - 110, cy - 162, "out", "callee: not in the impact", "left"),
    }
    edges = [("order_total", "compute_tax"), ("test_tax_rounds_half_up", "compute_tax"),
             ("checkout", "order_total"), ("test_order_total", "order_total"), ("order_total", "apply_discount")]
    for a, c in edges:
        x1, y1 = nodes[a][:2]
        x2, y2 = nodes[c][:2]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        pad_end = 16 if c == "compute_tax" else 12
        sx, sy = x1 + dx / L * 9, y1 + dy / L * 9
        ex, ey = x2 - dx / L * pad_end, y2 - dy / L * pad_end
        b.append(A(p, f"M{sx:.1f} {sy:.1f}L{ex:.1f} {ey:.1f}", "e-faded" if nodes[c][2] == "out" else ""))
    for name, (x, y, kind, sub, where) in nodes.items():
        r = 11 if kind == "hit" else 7
        b.append(f'<circle cx="{x}" cy="{y}" r="{r}" class="n-{kind}"/>')
        title_cls = "t-sub t-mono" if kind == "out" else "t-title t-mono"
        if where == "below":
            b.append(T(x, y + 26, name, title_cls))
            b.append(T(x, y + 41, sub, "t-note"))
        elif where == "right":
            b.append(T(x + 13, y - 2, name, title_cls, "start"))
            b.append(T(x + 13, y + 13, sub, "t-note", "start"))
        else:
            b.append(T(x - 13, y - 2, name, title_cls, "end"))
            b.append(T(x - 13, y + 13, sub, "t-note", "end"))
    b.append(T(cx, 446, "arrows point from caller to callee", "t-note"))
    return (f'<div class="scroll"><svg viewBox="0 0 600 456" role="img" aria-label="Code graph around compute_tax: '
            f'order_total and a test call it directly; checkout and another test reach it in two hops; '
            f'apply_discount is a callee and not part of the impact." style="min-width:480px">{"".join(b)}</svg></div>')


# ── Fig 7: hybrid search ─────────────────────────────────────────────────────
def fig_hybrid():
    p = "f7"
    b = [defs(p)]
    b.append(box(20, 70, 200, 80, [("search_docs", "t-title"), ("“money must be", "t-sub"),
                                    ("integer cents”", "t-sub")]))
    b.append(A(p, "M220 100H250V52H278"))
    b.append(A(p, "M220 120H250V168H278"))
    b.append(box(280, 10, 300, 84, [("dense ranking (meaning)", "t-title"), ("1  CONTRIBUTING.md · Money", "t-sub t-mono"),
                                     ("2  pricing.py · compute_tax", "t-sub t-mono"),
                                     ("3  pricing.py · apply_discount", "t-sub t-mono")]))
    b.append(box(280, 126, 300, 84, [("sparse ranking (exact words)", "t-title"),
                                      ("1  CONTRIBUTING.md · Money", "t-sub t-mono"),
                                      ("2  CONTRIBUTING.md · Passwords", "t-sub t-mono"),
                                      ("3  models.py · Product", "t-sub t-mono")]))
    b.append(A(p, "M580 52H610V100H638"))
    b.append(A(p, "M580 168H610V120H638"))
    b.append(box(640, 60, 300, 100, [("reciprocal rank fusion", "t-title"), ("score = Σ 1 / (k + rank)", "t-sub t-mono"),
                                      ("ranked high in both → wins", "t-sub"),
                                      ("returns path:lines + text", "t-sub")], "d-store"))
    return svg(960, 220, "Hybrid search: one query is ranked twice, by meaning and by exact words, and the two "
               "rankings are fused so results strong in both come first.", "".join(b), 760)


# ── Fig 8: merge and judge ───────────────────────────────────────────────────
def fig_merge():
    p = "f8"
    b = [defs(p)]
    raw = [("security", "repository.py:8", "SQL injection", "critical · 0.75", "d-llm"),
           ("static", "repository.py:8", "S608 string-built SQL", "critical · 0.90", "d-box"),
           ("reviewer", "utils.py:16", "division by zero", "warning · 0.75", "d-llm"),
           ("tests", "utils.py:15", "no test for average()", "info · 0.60", "d-llm")]
    b.append(T(150, 18, "raw findings (4)", "t-groupt"))
    ry = [30, 96, 162, 228]
    for (ag, loc, title, meta, cls), y in zip(raw, ry):
        b.append(box(20, y, 260, 56, [(f"{ag} · {loc}", "t-title"), (f"{title} · {meta}", "t-sub")], cls))
    b.append(T(470, 18, "after merging (3)", "t-groupt"))
    merged = [(43, 96, "repository.py:8 · critical", "S608, reported by static + security",
               "confidence 0.90 → 0.94"),
              (162, 56, "utils.py:16 · warning", "division by zero · reviewer", ""),
              (228, 56, "utils.py:15 · info", "no test for average() · tests", "")]
    for top, h, t1, t2, t3 in merged:
        lines = [(t1, "t-title"), (t2, "t-sub")] + ([(t3, "t-ok")] if t3 else [])
        b.append(box(340, top, 260, h, lines))
    b.append(A(p, "M280 58H310V80H338"))
    b.append(A(p, "M280 124H310V102H338"))
    b.append(A(p, "M280 190H338"))
    b.append(A(p, "M280 256H338"))
    b.append(T(312, 150, "same file, ±3 lines", "t-note", "start"))
    # critic
    b.append(T(800, 18, "critic", "t-groupt"))
    b.append(box(660, 30, 280, 254, [("judges each merged finding", "t-title"),
                                       ("kept if the judge agrees", "t-sub"),
                                       ("and confidence ≥ 0.6", "t-sub"),
                                       ("severity can move up or down", "t-sub"),
                                       ("static-only findings skip this", "t-sub"),
                                       ("", "t-sub"),
                                       ("verdict", "t-title"),
                                       ("critical kept → request changes", "t-sub"),
                                       ("anything else kept → comment", "t-sub"),
                                       ("nothing kept → approve", "t-sub")], "d-llm"))
    for y in (91, 190, 256):
        b.append(A(p, f"M600 {y}H658", "e-llm", "l"))
    return svg(960, 300, "Merging: two findings on repository.py line 8 from different agents become one with higher "
               "confidence; the critic then keeps or drops each merged finding and sets the verdict.",
               "".join(b), 760)


# ── Fig 9: retry / DLQ state machine ─────────────────────────────────────────
def fig_retry():
    p = "f9"
    b = [defs(p)]
    b.append(box(20, 40, 160, 60, [("poll one message", "t-title"), ("max_poll_records = 1", "t-sub")]))
    b.append(A(p, "M180 70H218"))
    b.append(box(220, 40, 170, 60, [("run handler", "t-title"), ("agent, aggregator, …", "t-sub")]))
    b.append(A(p, "M390 70H758", "e-ok", "o"))
    b.append(T(575, 62, "success", "t-note"))
    b.append(box(760, 40, 180, 60, [("commit offset", "t-title"), ("only after success", "t-sub")]))
    b.append(A(p, "M305 100V148", "e-bad", "b"))
    b.append(T(315, 128, "exception", "t-note", "start"))
    b.append('<path d="M235 190L305 150L375 190L305 230Z" class="d-box"/>')
    b.append(T(305, 186, "attempt", "t-title"))
    b.append(T(305, 202, "< 3 ?", "t-sub"))
    b.append(A(p, "M235 190H182"))
    b.append(T(208, 182, "yes", "t-note"))
    b.append(box(30, 162, 150, 56, [("back off", "t-title"), ("2 s, then 4 s", "t-sub")]))
    b.append(A(p, "M105 162V130H260V102"))
    b.append(A(p, "M375 190H438", "e-bad", "b"))
    b.append(T(406, 182, "no", "t-note"))
    b.append(box(440, 160, 260, 60, [("publish to review.dlq", "t-title"),
                                       ("headers: origin_topic, service, error", "t-sub")], "d-bad"))
    b.append(A(p, "M700 190H850V102"))
    return svg(960, 250, "Message handling: success commits the offset; an exception retries up to three times with "
               "backoff, then the message goes to the dead-letter topic and the offset is committed.",
               "".join(b), 760)


# ── Fig 10: KEDA loop ────────────────────────────────────────────────────────
def fig_keda():
    p = "fa"
    b = [defs(p)]
    b.append(box(20, 30, 190, 72, [("Kafka", "t-title"), ("consumer-group lag", "t-sub"),
                                    ("messages not yet committed", "t-sub")], "d-cell"))
    b.append(A(p, "M210 66H258"))
    b.append(T(234, 58, "read", "t-note"))
    b.append(box(260, 30, 210, 72, [("KEDA scaler", "t-title"), ("desired = lag ÷ threshold", "t-sub"),
                                     ("threshold 2 (Claude agents)", "t-sub")]))
    b.append(A(p, "M470 66H518"))
    b.append(box(520, 30, 190, 72, [("HPA", "t-title"), ("min 1 · max 6 replicas", "t-sub"),
                                     ("6 = partition count", "t-sub")]))
    b.append(A(p, "M710 66H758"))
    b.append(box(760, 30, 180, 72, [("Deployment", "t-title"), ("agent pods", "t-sub"),
                                     ("join the consumer group", "t-sub")], "d-llm"))
    b.append(A(p, "M850 102V140H115V104", "e-kafka", "k"))
    b.append(T(480, 158, "more consumers drain the lag; after 300 s quiet KEDA scales back to 1", "t-note"))
    return svg(960, 170, "KEDA loop: consumer lag in Kafka drives the desired replica count, capped at six, and the "
               "added pods drain the lag.", "".join(b), 760)


# ── Fig 11: measured autoscaling (small multiples) ───────────────────────────
def fig_scale_chart():
    t = [0, 10, 20, 30, 40, 51, 61, 71, 81, 91, 101, 111, 121]
    reps = [1, 5, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6]
    lag = [None, 12, 2.4, 2.4, 2, 0, 0, 0, 0, 0, 0, 0, 0]

    def panel(ox, title, ys, ymax, ticks, unit, notes):
        W, H, L, R, TOP, B = 440, 230, 44, 16, 34, 36
        pw, ph = W - L - R, H - TOP - B
        sx = lambda v: ox + L + v / 130 * pw
        sy = lambda v: TOP + ph - v / ymax * ph
        o = [T(ox + L, 18, title, "t-title", "start")]
        for tk in ticks:
            o.append(f'<line x1="{ox + L}" x2="{ox + L + pw}" y1="{sy(tk):.1f}" y2="{sy(tk):.1f}" class="grid"/>')
            o.append(T(ox + L - 8, round(sy(tk) + 4, 1), f"{tk:g}", "t-axis", "end"))
        for xt in (0, 30, 60, 90, 120):
            o.append(T(round(sx(xt), 1), H - 16, f"{xt}s", "t-axis"))
        o.append(f'<line x1="{ox + L}" x2="{ox + L + pw}" y1="{sy(0):.1f}" y2="{sy(0):.1f}" class="axis"/>')
        pts = [(x, y) for x, y in zip(t, ys) if y is not None]
        d = "M" + " L".join(f"{sx(x):.1f} {sy(y):.1f}" for x, y in pts)
        o.append(f'<path d="{d}" class="series"/>')
        for x, y in pts:
            o.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4.5" class="pt"><title>{x} s after the burst: '
                     f'{y:g} {unit}</title></circle>')
        for x, y, txt, anchor, dx, dy in notes:
            o.append(T(round(sx(x) + dx, 1), round(sy(y) + dy, 1), txt, "t-note", anchor))
        return "".join(o)

    body = panel(0, "Agent replicas (agent-reviewer)", reps, 6.6, [0, 2, 4, 6], "replicas",
                 [(20, 6, "6 = partition cap", "start", 8, -9), (0, 1, "1 before", "start", 8, -8)])
    body += panel(490, "Lag per replica (KEDA metric)", lag, 13.5, [0, 4, 8, 12], "messages per replica",
                  [(10, 12, "12 at first sample", "start", 9, 4), (51, 0, "drained by ~50 s", "start", 6, -10)])
    return (f'<div class="scroll"><svg viewBox="0 0 930 230" role="img" aria-label="Measured on kind: replicas rose '
            f'from 1 to 5 within 10 seconds and 6 within 20 seconds; lag per replica fell from 12 to 0 by about '
            f'50 seconds." style="min-width:720px">{body}</svg></div>')


COMPONENTS = {
    "github": ("GitHub", "Source of events and destination of reviews.",
               ["Sends <code>pull_request</code> webhooks (opened, synchronize, reopened, ready_for_review) and "
                "<code>push</code> webhooks.",
                "Receives exactly one review per PR head commit."],
               "external", "gateway/app.py · common/github.py"),
    "gateway": ("gateway", "Front door: webhooks, REST API and the dashboard.",
                ["Verifies <code>X-Hub-Signature-256</code>; refuses unsigned webhooks unless "
                 "<code>ALLOW_UNSIGNED_WEBHOOKS=true</code>.",
                 "Fetches changed files + patches from the GitHub API and builds a <code>ReviewJob</code>.",
                 "Dedupes redeliveries: <code>SETNX dedupe:{repo}:{pr}:{head_sha}</code>.",
                 "Writes <code>job:{id}</code> and <code>status=queued</code> to Redis, then publishes to "
                 "<code>pr.review.requested</code> keyed by job_id.",
                 "Push to the default branch publishes <code>repo.index.requested</code> with the changed paths."],
                "HPA on CPU, 2–6 pods", "src/graphreview/gateway/app.py"),
    "agents": ("agents", "Four specialists that review the same job in parallel.",
               ["Each role subscribes with its own consumer group, so every role sees every job.",
                "Replicas inside one role split the six partitions.",
                "An agent that crashes still publishes an <code>AgentResult</code> with an error, so the "
                "aggregator never waits forever."],
               "KEDA on consumer lag, 1–6 pods per role", "src/graphreview/agents/service.py"),
    "static": ("static agent", "Deterministic linting, no LLM.",
               ["Writes the changed <code>.py</code> files (PR head) to a temp dir and runs "
                "<code>ruff</code> with rules F, E9, B, S, ASYNC, BLE, PERF.",
                "Keeps only diagnostics on lines the PR added.",
                "Maps rule codes to severity (F821 undefined name → critical, S6xx → critical security …).",
                "Its findings skip the critic: they are precise by construction."],
               "KEDA, lag threshold 5", "src/graphreview/agents/static_agent.py"),
    "reviewer": ("reviewer agent", "Correctness reviewer running the Claude tool loop.",
                 ["Focus: broken contracts with callers, edge cases, error handling, resource leaks, performance, "
                  "documented conventions.",
                  "Effort <code>high</code>; all five tools.",
                  "Two replicas by default in compose."],
                 "KEDA, lag threshold 2", "agents/llm_agent.py · agents/specs.py"),
    "security": ("security agent", "Application-security reviewer, same loop.",
                 ["Focus: SQL/shell injection, missing authorization, secrets, unsafe deserialization, SSRF, "
                  "path traversal, weak crypto.",
                  "Effort <code>high</code>; all five tools; traces input from handlers to sinks with the graph."],
                 "KEDA, lag threshold 2", "agents/llm_agent.py · agents/specs.py"),
    "tests": ("tests agent", "Finds changed behaviour that no test exercises.",
              ["Calls <code>find_tests</code> on changed symbols; suggests a short test sketch.",
               "Effort <code>medium</code>; tools: find_tests, get_callers, read_snippet, search_code."],
              "KEDA, lag threshold 3", "agents/llm_agent.py · agents/specs.py"),
    "aggregator": ("aggregator", "Joins the four results for a job and merges duplicates.",
                   ["Stores each result in Redis hash <code>agg:{id}</code> (one field per agent, so redelivery is "
                    "harmless) and per-agent progress for the dashboard.",
                    "When all expected agents reported: cluster findings (same file, ±3 lines, same category or "
                    "similar text), boost confidence when agents agree, publish <code>review.aggregated</code>.",
                    "A sweeper flushes partial results after 300 s and marks missing agents <code>timeout</code>.",
                    "<code>SETNX agg:done:{id}</code> makes the flush happen once across replicas."],
                   "replicas by partition (results are keyed by job_id)", "src/graphreview/aggregator/service.py"),
    "critic": ("critic", "LLM-as-judge over the merged findings.",
               ["One Claude call (effort <code>medium</code>) with the diff, packed graph context and the "
                "proposed findings.",
                "Returns keep/drop, confidence and severity per finding id, plus a summary and verdict.",
                "Drops anything below confidence 0.6. Static-only findings bypass it."],
               "KEDA, lag threshold 2", "src/graphreview/critic/service.py"),
    "publisher": ("publisher", "Delivers the review.",
                  ["Stores the <code>FinalReview</code> in Redis and sets <code>status=completed</code>.",
                   "For GitHub jobs posts one review, guarded by <code>SETNX posted:{id}</code>.",
                   "Findings on diff lines become inline comments; the rest (for example a broken caller in "
                   "another file) go in the review body.",
                   "Records webhook-to-review latency."],
                  "1–2 pods", "src/graphreview/publisher/service.py"),
    "indexer": ("indexer", "Builds the two indexes the agents search.",
                ["Shallow clone; the GitHub token travels as an HTTP header, never in the remote URL.",
                 "Graph: <code>graphify extract --code-only</code>, falling back to the built-in Python AST "
                 "builder. Paths normalised to repo-relative.",
                 "Chunks code (AST-aligned) and docs (by heading), embeds dense + BM25, upserts into Qdrant.",
                 "On push, only changed paths are re-embedded."],
                "1 pod; work is per repository", "indexer/service.py · retrieval/graph_builder.py · retrieval/chunker.py"),
    "redis": ("Redis", "Shared state, so services stay stateless.",
              ["<code>job:{id}</code>, <code>status:{id}</code>, <code>progress:{id}</code>, <code>review:{id}</code>",
               "<code>agg:{id}</code> + the <code>agg:deadlines</code> sorted set",
               "Guards: <code>dedupe:*</code>, <code>agg:done:*</code>, <code>posted:*</code>",
               "<code>graph:{repo}</code> gzip blob + <code>graph:{repo}:version</code> for cache invalidation"],
              "StatefulSet + 2 Gi volume", "src/graphreview/common/state.py"),
    "qdrant": ("Qdrant", "Vector store for code and doc chunks.",
               ["Collection <code>code_chunks</code> with two named vectors: <code>dense</code> (cosine) and "
                "<code>sparse</code> (BM25 with IDF).",
                "Payload per chunk: repo, path, start/end line, kind (code or doc), symbol, text.",
                "Queries fuse a dense and a sparse prefetch with reciprocal rank fusion."],
               "StatefulSet + 10 Gi volume", "src/graphreview/retrieval/vector_store.py"),
    "context": ("context service", "The GraphRAG API every agent tool calls.",
                ["Endpoints: <code>/v1/impact</code>, <code>callers</code>, <code>find_tests</code>, "
                 "<code>search_code</code>, <code>search_docs</code>, <code>read_snippet</code>, "
                 "<code>pack_context</code>.",
                 "Loads the graph from Redis and caches it per version; stateless, so it scales horizontally.",
                 "Same tools exposed over MCP (<code>graphreview mcp</code>) for Claude Code or Cursor."],
                "HPA on CPU, 2–8 pods", "context/api.py · retrieval/retriever.py · retrieval/graph.py"),
    "claude": ("Claude API", "The model behind the reviewer, security, tests and critic agents.",
               ["Messages API with structured output (a JSON schema), strict tool schemas, top-level prompt "
                "caching and server-side refusal fallback.",
                "Default model <code>claude-opus-5-5</code> (<code>AGENT_MODEL</code>, <code>CRITIC_MODEL</code>).",
                "With <code>LLM_BACKEND=fake</code> a rule engine answers instead, using the same response "
                "shape, so the whole pipeline runs without a key."],
               "external", "src/graphreview/common/llm.py"),
}


KIND = {"github": ("external", "k-ext"), "claude": ("external · Claude", "k-llm"), "redis": ("storage", "k-store"),
        "qdrant": ("storage", "k-store"), "reviewer": ("agent · Claude", "k-llm"), "security": ("agent · Claude", "k-llm"),
        "tests": ("agent · Claude", "k-llm"), "critic": ("service · Claude", "k-llm"), "static": ("agent · no LLM", "k-svc"),
        "agents": ("group of 4", "k-svc")}
ORDER = ["github", "gateway", "agents", "static", "reviewer", "security", "tests", "aggregator", "critic",
         "publisher", "indexer", "redis", "qdrant", "context", "claude"]


def cards():
    out = []
    for cid in ORDER:
        title, what, items, scale, code = COMPONENTS[cid]
        kind, kcls = KIND.get(cid, ("service", "k-svc"))
        lis = "".join(f"<li>{i}</li>" for i in items)
        out.append(f'''<article class="card" id="c-{cid}" tabindex="-1">
  <p class="kind {kcls}">{esc(kind)}</p>
  <h4>{esc(title)}</h4>
  <p class="what">{esc(what)}</p>
  <ul>{lis}</ul>
  <dl><dt>Scales</dt><dd>{esc(scale)}</dd><dt>Code</dt><dd>{esc(code)}</dd></dl>
  <button type="button" class="back" data-node="{cid}">Show on the map ↑</button>
</article>''')
    return "\n".join(out)


FIGURES = [  # (placeholder in template.html, file name, builder)
    ("FIG_SYSTEM", "01-system-map", fig_system),
    ("FIG_SEQUENCE", "02-review-sequence", fig_sequence),
    ("FIG_KAFKA", "03-kafka-consumer-groups", fig_kafka),
    ("FIG_LOOP", "04-agent-tool-loop", fig_agent_loop),
    ("FIG_INDEX", "05-indexing", fig_index),
    ("FIG_GRAPH", "06-code-graph-impact", fig_graph),
    ("FIG_HYBRID", "07-hybrid-search", fig_hybrid),
    ("FIG_MERGE", "08-merge-and-judge", fig_merge),
    ("FIG_RETRY", "09-retry-and-dlq", fig_retry),
    ("FIG_KEDA", "10-keda-loop", fig_keda),
    ("FIG_SCALE_CHART", "11-keda-burst-measured", fig_scale_chart),
]

LIGHT = {"paper": "#f5f6f3", "panel": "#ffffff", "wash": "#eceee9", "rule": "#d6dbd4", "ink": "#17201c",
         "ink-2": "#46524c", "ink-3": "#6a756f", "kafka": "#0b6973", "kafka-wash": "#dcefef", "llm": "#9a4c0c",
         "llm-wash": "#fbece0", "store": "#5447a6", "store-wash": "#ebe9f8", "ok": "#1c7a3d", "bad": "#b42318",
         "bad-wash": "#fbe9e7"}
DARK = {"paper": "#101513", "panel": "#161d1a", "wash": "#1d2521", "rule": "#2c3631", "ink": "#e4eae6",
        "ink-2": "#b3beb8", "ink-3": "#8a958f", "kafka": "#5cc6d0", "kafka-wash": "#11292c", "llm": "#eba35f",
        "llm-wash": "#2c2016", "store": "#a99ef0", "store-wash": "#211f34", "ok": "#5cc785", "bad": "#ff8b80",
        "bad-wash": "#331a18"}
PAD = 16


def standalone_svg(fragment: str, svg_css: str) -> str:
    """Turn an inline page figure into a self-contained SVG file (own styles, background, both themes)."""
    m = re.search(r'<svg viewBox="0 0 (\d+) (\d+)" (role="img" aria-label="[^"]*")[^>]*>(.*)</svg>', fragment, re.S)
    w, h, aria, body = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
    body = body.replace(' tabindex="0" role="button"', "")  # click targets only make sense on the HTML page
    tokens = lambda t: "".join(f"--{k}:{v};" for k, v in t.items())
    css = (f"svg{{{tokens(LIGHT)}--body:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;"
           f"--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}"
           f"@media (prefers-color-scheme:dark){{svg{{{tokens(DARK)}}}}}"
           f".bg{{fill:var(--panel);stroke:var(--rule)}}{svg_css}")
    W, H = w + 2 * PAD, h + 2 * PAD
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-PAD} {-PAD} {W} {H}" width="{W}" height="{H}" {aria}>'
            f"<style>{css}</style>"
            f'<rect class="bg" x="{-PAD + 0.5}" y="{-PAD + 0.5}" width="{W - 1}" height="{H - 1}" rx="12"/>'
            f"{body}</svg>\n")


def build():
    tpl = (HERE / "template.html").read_text()
    svg_css = re.search(r"/\* svg vocabulary \*/(.*?)/\*", tpl, re.S).group(1)
    svg_css = re.sub(r"\s*\n\s*", "", svg_css)
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    for key, name, fn in FIGURES:
        fragment = fn()
        tpl = tpl.replace("{{" + key + "}}", fragment)
        (OUT_SVG / f"{name}.svg").write_text(standalone_svg(fragment, svg_css))
    tpl = tpl.replace("{{COMPONENT_CARDS}}", cards())
    assert "{{" not in tpl, "unfilled placeholder"
    head, body = tpl.split('<div class="page">', 1)
    doc = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
           f'{head}</head>\n<body>\n<div class="page">{body}</body>\n</html>\n')
    OUT_HTML.write_text(doc)
    print(f"wrote {OUT_HTML.relative_to(DOCS.parent)} and {len(FIGURES)} SVGs in {OUT_SVG.relative_to(DOCS.parent)}")


if __name__ == "__main__":
    build()
