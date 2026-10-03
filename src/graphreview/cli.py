"""`graphreview` command line: run any service, or the whole pipeline in-process."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import subprocess
import sys
from pathlib import Path

from graphreview.common.config import Settings, get_settings
from graphreview.common.telemetry import setup_logging, start_metrics

log = logging.getLogger("graphreview")


async def _serve_worker(make_coro, settings: Settings) -> None:
    """Run a bus worker until SIGTERM/SIGINT, then shut down cleanly."""
    from graphreview.common.bus import make_bus

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    bus = make_bus(settings, client_id="graphreview-worker")
    await bus.start()
    task = asyncio.create_task(make_coro(bus, stop))
    await asyncio.wait([task, asyncio.create_task(stop.wait())], return_when=asyncio.FIRST_COMPLETED)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await bus.stop()
    if task.done() and not task.cancelled() and task.exception():
        raise task.exception()


def cmd_worker(args, settings: Settings) -> None:
    from graphreview.common.github import GitHubClient
    from graphreview.common.llm import make_llm
    from graphreview.common.state import make_store
    from graphreview.context.client import HttpContextClient

    start_metrics(settings.metrics_port)

    async def run(bus, stop):
        store = make_store(settings)
        if args.cmd == "agent":
            from graphreview.agents.service import build_agent, run_agent_service
            from graphreview.agents.tools import GitHubFileSource

            files = GitHubFileSource(GitHubClient(settings.github_token, settings.github_api_url))
            agent = build_agent(args.role, settings, make_llm(settings),
                                HttpContextClient(settings.context_service_url), files)
            await run_agent_service(bus, agent, settings, stop)
        elif args.cmd == "aggregator":
            from graphreview.aggregator.service import Aggregator

            await Aggregator(bus, store, settings.aggregation_timeout_s, settings.expected_agents).run(
                settings.handler_max_attempts, stop)
        elif args.cmd == "critic":
            from graphreview.critic.service import Critic

            await Critic(bus, make_llm(settings), HttpContextClient(settings.context_service_url),
                         model=settings.critic_model, min_confidence=settings.critic_min_confidence).run(
                settings.handler_max_attempts, stop)
        elif args.cmd == "publisher":
            from graphreview.publisher.service import Publisher

            gh = GitHubClient(settings.github_token, settings.github_api_url) if settings.github_token else None
            await Publisher(bus, store, gh, settings.github_review_event).run(settings.handler_max_attempts, stop)
        elif args.cmd == "indexer":
            from graphreview.indexer.service import Indexer
            from graphreview.retrieval.embeddings import make_embedder
            from graphreview.retrieval.retriever import GraphRepository
            from graphreview.retrieval.vector_store import VectorStore

            vectors = VectorStore(settings.qdrant_url, settings.qdrant_collection, make_embedder(settings))
            await Indexer(GraphRepository(store), vectors, repos_dir=settings.repos_dir,
                          github_token=settings.github_token, graphify_enabled=settings.graphify_enabled,
                          graphify_timeout_s=settings.graphify_timeout_s).run(bus, settings.handler_max_attempts, stop)

    asyncio.run(_serve_worker(run, settings))


def cmd_http(args, settings: Settings) -> None:
    import uvicorn

    if args.cmd == "gateway":
        from graphreview.gateway.app import create_app
    else:
        from graphreview.context.api import create_app
    uvicorn.run(create_app(), host=args.host, port=args.port, log_config=None, proxy_headers=True)


def _git_diff(repo_path: Path, base: str) -> str:
    return subprocess.run(["git", "-C", str(repo_path), "diff", base], capture_output=True, text=True,
                          check=True).stdout


def cmd_review_local(args, settings: Settings) -> None:
    from graphreview.common.diff import split_git_diff
    from graphreview.common.models import ChangedFile, ReviewJob
    from graphreview.pipeline import LocalPipeline
    from graphreview.publisher.service import render_markdown

    repo_path = Path(args.repo_path).resolve()
    diff = Path(args.diff).read_text() if args.diff else _git_diff(repo_path, args.base)
    files = [ChangedFile(path=p.path, status=p.status, additions=p.additions, deletions=p.deletions, patch=p.patch)
             for p in split_git_diff(diff)]
    if not files:
        sys.exit("No changes to review (empty diff).")
    repo = args.repo or f"local/{repo_path.name}"

    async def main():
        async with LocalPipeline(settings) as pipe:
            idx = await pipe.index(repo, repo_path)
            print(f"indexed {repo}: {idx.graph_nodes} graph nodes ({idx.graph_source}), {idx.chunks} chunks",
                  file=sys.stderr)
            job = ReviewJob(source="local", repo=repo, title=args.title, files=files, local_path=str(repo_path),
                            context_mode=args.mode)
            final = await pipe.review(job)
            print(render_markdown(final))

    asyncio.run(main())


def cmd_demo(args, settings: Settings) -> None:
    """Seed a bug from the eval set into a copy of the fixture repo and review it end to end."""
    import json
    import tempfile

    from graphreview.common.models import ReviewJob
    from graphreview.eval.cases import EVAL_REPO, materialize
    from graphreview.pipeline import LocalPipeline
    from graphreview.publisher.service import render_markdown

    case_file = next(Path(args.cases).glob(f"{args.case}*.json"), None)
    if case_file is None:
        sys.exit(f"no case matching {args.case!r} in {args.cases}")
    case = json.loads(case_file.read_text())
    fixture = Path(args.fixture)

    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            head, files, _ = materialize(case, fixture, Path(tmp))
            async with LocalPipeline(settings) as pipe:
                idx = await pipe.index(EVAL_REPO, fixture)
                print(f"indexed {EVAL_REPO}: {idx.graph_nodes} nodes / {idx.graph_edges} edges ({idx.graph_source}),"
                      f" {idx.chunks} chunks · llm={settings.llm_backend} embeddings={settings.embedding_backend}\n"
                      f"PR: \"{case['title']}\"\n", file=sys.stderr)
                for f in files:
                    print(f"--- {f.path}\n{f.patch}\n", file=sys.stderr)
                final = await pipe.review(ReviewJob(source="local", repo=EVAL_REPO, title=case["title"],
                                                    body=case.get("body", ""), files=files, local_path=str(head)))
                print(render_markdown(final))

    asyncio.run(main())


def cmd_submit(args, settings: Settings) -> None:
    """Client for a running stack: post a seeded-bug PR to the gateway and wait for the review."""
    import json
    import tempfile
    import time

    import httpx

    from graphreview.common.models import FinalReview
    from graphreview.eval.cases import EVAL_REPO, materialize
    from graphreview.publisher.service import render_markdown

    case_file = next(Path(args.cases).glob(f"{args.case}*.json"), None)
    if case_file is None:
        sys.exit(f"no case matching {args.case!r} in {args.cases}")
    case = json.loads(case_file.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        head, files, _ = materialize(case, Path(args.fixture), Path(tmp))
        diff = "".join(f"diff --git a/{f.path} b/{f.path}\n--- a/{f.path}\n+++ b/{f.path}\n{f.patch}\n" for f in files)
        head_files = {f.path: (head / f.path).read_text() for f in files}
    with httpx.Client(base_url=args.gateway, timeout=30) as http:
        r = http.post("/api/reviews/diff", json={"repo": args.repo or EVAL_REPO, "diff": diff, "title": case["title"],
                                                 "body": case.get("body", ""), "head_files": head_files})
        r.raise_for_status()
        job_id = r.json()["job_id"]
        print(f"submitted \"{case['title']}\" → job {job_id}", file=sys.stderr)
        t0 = time.monotonic()
        while time.monotonic() - t0 < args.timeout:
            data = http.get(f"/api/reviews/{job_id}").json()
            if data.get("status") == "completed":
                print(f"completed in {time.monotonic() - t0:.1f}s\n", file=sys.stderr)
                print(render_markdown(FinalReview.model_validate(data["review"])))
                return
            time.sleep(1)
    sys.exit(f"job {job_id} not completed after {args.timeout}s (check `docker compose logs`)")


def cmd_seed_case(args, settings: Settings) -> None:
    """Apply a seeded-bug case's edits in place to a checkout (used to open demo PRs on GitHub)."""
    import json

    case_file = next(Path(args.cases).glob(f"{args.case}*.json"), None)
    if case_file is None:
        sys.exit(f"no case matching {args.case!r}")
    case = json.loads(case_file.read_text())
    root = Path(args.path)
    for e in case["edits"]:
        p = root / e["file"]
        text = p.read_text()
        if text.count(e["find"]) != 1:
            sys.exit(f"{e['file']}: edit anchor not found exactly once (checkout already modified?)")
        p.write_text(text.replace(e["find"], e["replace"]))
    print(json.dumps({"id": case["id"], "title": case["title"], "body": case.get("body", "")}))


def cmd_index(args, settings: Settings) -> None:
    from graphreview.common.models import IndexRequest
    from graphreview.common.state import make_store
    from graphreview.indexer.service import Indexer
    from graphreview.retrieval.embeddings import make_embedder
    from graphreview.retrieval.retriever import GraphRepository
    from graphreview.retrieval.vector_store import VectorStore

    async def main():
        store = make_store(settings)
        vectors = VectorStore(settings.qdrant_url, settings.qdrant_collection, make_embedder(settings))
        res = await Indexer(GraphRepository(store), vectors, graphify_enabled=settings.graphify_enabled).index(
            IndexRequest(repo=args.repo, local_path=str(Path(args.path).resolve())))
        print(res.model_dump_json(indent=2))
        await store.close()

    asyncio.run(main())


def cmd_eval(args, settings: Settings) -> None:
    from graphreview.eval.harness import markdown_table, run_eval

    report = asyncio.run(run_eval(settings, Path(args.cases), Path(args.fixture), args.modes.split(","),
                                  Path(args.out), args.only.split(",") if args.only else None))
    print("\n" + markdown_table(report))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="graphreview", description=__doc__)
    p.add_argument("--llm", choices=["anthropic", "fake"], help="override LLM_BACKEND")
    p.add_argument("--embeddings", choices=["fastembed", "hash"], help="override EMBEDDING_BACKEND")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("gateway", "context"):
        sp = sub.add_parser(name, help=f"run the {name} HTTP service")
        sp.add_argument("--host", default="0.0.0.0")  # noqa: S104 — container entrypoint
        sp.add_argument("--port", type=int, default=8080 if name == "gateway" else 8001)
    sp = sub.add_parser("agent", help="run a review agent worker")
    sp.add_argument("--role", required=True, choices=["static", "reviewer", "security", "tests"])
    for name in ("aggregator", "critic", "publisher", "indexer"):
        sub.add_parser(name, help=f"run the {name} worker")

    sp = sub.add_parser("index", help="index a local checkout into the configured stores")
    sp.add_argument("--repo", required=True)
    sp.add_argument("--path", required=True)

    sp = sub.add_parser("review-local", help="review a local diff with the whole pipeline in-process")
    sp.add_argument("--repo-path", default=".")
    sp.add_argument("--repo", help="repo name (default local/<dir>)")
    sp.add_argument("--diff", help="unified diff file (default: `git diff <base>`)")
    sp.add_argument("--base", default="HEAD")
    sp.add_argument("--title", default="Local changes")
    sp.add_argument("--mode", default="graph_rag", choices=["graph_rag", "diff_only", "full_files"])

    sp = sub.add_parser("demo", help="seed a bug into the sample repo and review it in-process")
    sp.add_argument("--case", default="01", help="eval case id prefix, e.g. 01 (SQL injection), 05 (breaks callers)")
    sp.add_argument("--cases", default="eval/cases")
    sp.add_argument("--fixture", default="eval/fixtures/shopapp")

    sp = sub.add_parser("submit", help="send a seeded-bug PR to a running gateway and print the review")
    sp.add_argument("--case", default="01")
    sp.add_argument("--gateway", default="http://localhost:8080")
    sp.add_argument("--repo", help="indexed repo name (default eval/shopapp)")
    sp.add_argument("--timeout", type=int, default=300)
    sp.add_argument("--cases", default="eval/cases")
    sp.add_argument("--fixture", default="eval/fixtures/shopapp")

    sp = sub.add_parser("seed-case", help="apply a seeded-bug case to a checkout in place; prints title/body JSON")
    sp.add_argument("--case", required=True)
    sp.add_argument("--path", required=True)
    sp.add_argument("--cases", default="eval/cases")

    sp = sub.add_parser("eval", help="run the seeded-bug benchmark")
    sp.add_argument("--cases", default="eval/cases")
    sp.add_argument("--fixture", default="eval/fixtures/shopapp")
    sp.add_argument("--modes", default="graph_rag,full_files,diff_only")
    sp.add_argument("--only", help="comma-separated case ids")
    sp.add_argument("--out", default="eval/results")

    sp = sub.add_parser("mcp", help="serve the GraphRAG tools over MCP (stdio)")
    sp.add_argument("--repo", required=True)
    sp.add_argument("--context-url", default="http://localhost:8001")

    args = p.parse_args(argv)
    overrides = {}
    if args.llm:
        overrides["llm_backend"] = args.llm
    if args.embeddings:
        overrides["embedding_backend"] = args.embeddings
    local = args.cmd in ("review-local", "eval", "demo")
    if local:  # in-process: no broker, no Redis
        overrides.update(bus_backend="memory", state_backend="memory", log_json=False)
    settings = Settings(**overrides) if overrides else get_settings()
    setup_logging(settings.log_level if not local else "WARNING", settings.log_json)

    if args.cmd in ("agent", "aggregator", "critic", "publisher", "indexer"):
        cmd_worker(args, settings)
    elif args.cmd in ("gateway", "context"):
        cmd_http(args, settings)
    elif args.cmd == "review-local":
        cmd_review_local(args, settings)
    elif args.cmd == "index":
        cmd_index(args, settings)
    elif args.cmd == "eval":
        cmd_eval(args, settings)
    elif args.cmd == "demo":
        cmd_demo(args, settings)
    elif args.cmd == "submit":
        cmd_submit(args, settings)
    elif args.cmd == "seed-case":
        cmd_seed_case(args, settings)
    elif args.cmd == "mcp":
        from graphreview.context.mcp_server import run

        run(args.repo, args.context_url)


if __name__ == "__main__":
    main()
