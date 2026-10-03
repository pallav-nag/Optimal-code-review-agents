# Demo runbook

Three ways to show GraphReview, from zero-setup to the full live flow.

| | Needs | Shows | Time to set up |
|---|---|---|---|
| **A. Terminal** | Python | whole pipeline in one process | 1 min |
| **B. Dashboard** | Docker | Kafka fan-out, live pipeline UI, Grafana | 3 min |
| **C. Live GitHub** | Docker + `gh` | real PRs get real inline review comments | 5 min |
| **D. Kubernetes** | Docker + kind | Strimzi Kafka, KEDA autoscaling on consumer lag | 8 min |

Every mode works without an API key: the rule-based stand-in plays the LLM. With `ANTHROPIC_API_KEY` set and
`LLM_BACKEND=anthropic`, Claude reviews the same PRs. The interesting cases (02, 03, 05, 08, 10) are the ones
only Claude catches.

---

## A. Terminal (no Docker)

```bash
make install && source .venv/bin/activate
make demo CASE=01          # SQL injection: security agent and ruff agree → merged, higher confidence
make demo-claude CASE=05   # with a key: compute_tax gains a parameter; the graph shows order_total/checkout break
```

## B. Dashboard

```bash
make up                    # 17 containers; first run copies .env.example → .env (keyless)
open http://localhost:8080
```

1. Click **Index sample repo**. The gateway publishes to `repo.index.requested`, the indexer builds the
   code graph and embeddings, and the header chip updates (60 nodes · 147 edges · 54 chunks).
2. Pick **05 · Make tax rates injectable** and click **Submit PR**.
3. Walk through the page:
   * **Pipeline stepper.** Gateway → Kafka fan-out to 4 consumer groups → aggregator joins by `job_id` →
     critic → published.
   * **Agents table.** Per-agent latency, tool calls (`find_tests`, `search_docs`, …) and tokens.
   * **What the code graph saw.** `compute_tax` → `order_total` → `checkout`, plus the tests reaching it. This
     is the context that lets an LLM flag the broken caller *outside* the diff.
4. Submit **01** (SQL injection). The finding is tagged `security` and `static`: two independent agents
   agreed, so the aggregator merged them and raised confidence.
5. Show http://localhost:3000 → dashboard *GraphReview*: throughput per service, LLM tokens per agent,
   cache-hit share, tool calls, retrieval latency, and findings by severity.

The same flow works from the terminal with `make stack-demo CASE=05`.

## C. Live GitHub PRs

One-time setup:

```bash
gh extension install cli/gh-webhook
# .env:
#   GITHUB_TOKEN=<output of `gh auth token`>
#   GITHUB_WEBHOOK_SECRET=<any random string, e.g. `openssl rand -hex 20`>
make up
./scripts/github-demo.sh          # VISIBILITY=public CASES="01 05" to customise
```

The script creates `<you>/shopapp-graphreview-demo`, forwards its webhooks to localhost, indexes it, and opens
seeded PRs. Each PR gets one review: inline comments on diff lines, plus a summary in the body.

## D. Kubernetes

```bash
make k8s-up                               # kind + Strimzi + KEDA + dev overlay
open http://localhost:8080                # same dashboard, served from the cluster
kubectl -n graphreview get pods,kafka,kafkatopics,scaledobjects
```

To show autoscaling, submit a burst of PRs and watch KEDA scale agents on consumer-group lag:

```bash
for i in $(seq 1 30); do curl -s -XPOST localhost:8080/api/demo/cases/01-sql-injection-email >/dev/null; done
kubectl -n graphreview get hpa -w
```

---

## Talking points (≈3 minutes)

1. **Problem.** LLM reviewers either see only the diff and miss breakage elsewhere, or you paste whole files
   and pay for noise. Reviewers need *structure*: who calls this, and what's tested.
2. **GraphRAG.** A code graph (Graphify, tree-sitter) answers structural questions. A hybrid vector index
   (dense + BM25, RRF) answers "find similar code" and "what are our conventions". Agents pull context on demand
   through tools instead of getting a fixed context dump.
3. **Agents.** Specialists run in parallel: correctness, security, tests, and static analysis. Each runs a
   bounded tool-use loop with schema-constrained output. A critic agent judges the merged findings and
   rejects false positives.
4. **Kafka.** Each agent role is its own consumer group, which gives fan-out for free. Delivery is
   at-least-once with idempotent consumers. Failures retry and then dead-letter. The aggregator state lives in
   Redis, so it survives restarts and runs as replicas.
5. **Kubernetes.** Strimzi runs Kafka. KEDA scales each agent role on its own consumer lag (the LLM agents are
   the slow, expensive tier). The manifests also include hardened pods, network policies, and PDBs.
6. **Evaluation.** A seeded-bug benchmark with an ablation (diff only vs full files vs GraphRAG) measures
   precision, recall, tokens and cost.
