# Command reference

Run everything from the project folder:

```bash
cd ~/Projects/Agents_codereview_Graphify
source .venv/bin/activate        # puts `graphreview`, `pytest`, `ruff` on PATH (make targets don't need it)
```

---

## 1. Stop everything (laptop heating up)

```bash
docker compose down                      # stops the 17 compose containers (data volumes kept)
kind delete cluster --name graphreview   # removes the Kubernetes (kind) cluster, if you created one
colima stop                              # shuts down the Docker VM — frees the 6 CPUs / 10 GB RAM
```

`colima stop` is the one that matters for heat/battery. Nothing auto-starts at login.

## 2. Start / recover

| What you ran | What's lost | Recover with | Wait |
|---|---|---|---|
| `colima stop` | nothing | `colima start` then `make up` | ~1–2 min |
| `docker compose down` | containers only | `make up` | ~1 min |
| `docker compose down -v` | containers + data (reviews, vector index, Kafka messages) | `make up`, then click **Index sample repo** in the dashboard (or `make stack-index`) | ~1 min |
| `kind delete cluster` | the whole Kubernetes cluster | `make k8s-up` | ~8–10 min |
| `colima delete` | everything Docker: images, build cache, volumes, kind cluster | `colima start --cpu 6 --memory 10 --disk 60 --vm-type vz --vz-rosetta` then `make up` | ~10–20 min (downloads + image rebuild) |

```bash
colima start                    # after `colima stop` (remembers 6 CPU / 10 GB)
colima start --cpu 6 --memory 10 --disk 60 --vm-type vz --vz-rosetta   # after `colima delete` — flags required again
colima status                   # is the VM running?
make up                         # compose stack → dashboard http://localhost:8080 · Grafana http://localhost:3000
make k8s-up                     # OR the kind cluster — not both at once, they share port 8080
```

Switch between compose and kind without rebuilding:

```bash
docker compose stop && docker start graphreview-control-plane    # compose → kind
docker stop graphreview-control-plane && docker compose start    # kind → compose
```

Not removed by `colima delete`: Homebrew tools, `~/.docker/config.json`, `.venv`, the code. Offline mode
(`make demo`, `make test`) needs no Docker at all.

## 3. Free disk space

```bash
docker compose down -v          # also deletes Kafka / Redis / Qdrant / repo-clone volumes
docker system prune -a          # deletes unused images + build cache (forces image rebuilds)
colima delete                   # removes the whole VM (~60 GB)
rm -rf .venv                    # local Python env (recreate with `make install`)
```

---

## 4. Setup (first time / new machine)

```bash
brew install colima docker docker-compose docker-buildx kind kubectl helm
mkdir -p ~/.docker && echo '{"cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]}' > ~/.docker/config.json
colima start --cpu 6 --memory 10 --disk 60 --vm-type vz --vz-rosetta
make install                    # creates .venv with all extras (~30 s)
cp .env.example .env            # `make up` does this automatically if .env is missing
```

## 5. Everyday development (no Docker needed)

```bash
make test                       # unit + in-process end-to-end tests
make lint                       # ruff
make demo                       # seeded SQL-injection PR through the whole pipeline in-process (offline)
make demo CASE=09               # any case 01–11 from eval/cases/
graphreview --llm fake --embeddings hash review-local --repo-path . --base HEAD   # review your own uncommitted changes
```

## 6. Running stack (Docker Compose)

```bash
make up                         # build + start (first run copies .env.example → .env)
make down                       # stop + remove containers (keeps data)
make logs                       # follow gateway / reviewer / aggregator / critic / publisher logs
docker compose logs -f indexer  # any single service
docker compose ps               # what's running
docker compose restart gateway  # restart one service
make images                     # rebuild both images without starting anything
docker compose up -d --build    # rebuild + restart after code changes
docker compose --profile tools up -d kafka-ui   # Kafka UI on http://localhost:8090

make stack-index                # index the sample repo into the stack (same as the dashboard button)
make stack-demo CASE=05         # submit a seeded-bug PR to the stack and print the review
```

URLs: dashboard http://localhost:8080 · API docs http://localhost:8080/docs · Grafana http://localhost:3000
(admin / admin) · Prometheus http://localhost:9090 · Qdrant http://localhost:6333/dashboard

### Gateway API

```bash
curl -XPOST localhost:8080/api/demo/index                                   # index the sample repo (demo mode)
curl -XPOST localhost:8080/api/demo/cases/01-sql-injection-email           # submit a seeded case (demo mode)
curl localhost:8080/api/reviews                                             # recent reviews
curl localhost:8080/api/reviews/<job_id>                                    # one review + live progress
curl localhost:8080/api/repos/eval/shopapp                                  # index status
curl -XPOST localhost:8080/api/repos/index -H 'content-type: application/json' -d '{"repo":"owner/name"}'
curl -XPOST localhost:8080/api/reviews     -H 'content-type: application/json' -d '{"repo":"owner/name","pr_number":123}'
```

## 7. Kubernetes (kind)

```bash
make k8s-up                     # kind + Strimzi + KEDA + metrics-server + app (dev overlay)
make k8s-down                   # delete the cluster
kubectl -n graphreview get pods
kubectl -n graphreview get kafka,kafkatopics,scaledobjects,hpa
kubectl -n graphreview logs -f deploy/agent-reviewer
kubectl top nodes               # resource usage

# reload code changes into the cluster
make images && kind load docker-image graphreview-core:dev graphreview-rag:dev --name graphreview
kubectl -n graphreview rollout restart deploy -l tier=app

# re-apply config changes (deploy/k8s/overlays/dev/config.env, secrets.env)
kubectl apply -k deploy/k8s/overlays/dev && kubectl -n graphreview rollout restart deploy -l tier=app

# autoscaling demo: burst 40 PRs, watch KEDA scale agents on consumer lag
for i in $(seq 1 40); do curl -s -o /dev/null -XPOST localhost:8080/api/demo/cases/01-sql-injection-email; done
kubectl -n graphreview get hpa -w

# wipe review state (keeps the cluster)
kubectl -n graphreview exec redis-0 -- redis-cli FLUSHDB
```

Validate manifests without a cluster: `kubectl kustomize deploy/k8s/overlays/prod`.

---

## 8. Switching on Claude (costs API credits)

In `.env`: `ANTHROPIC_API_KEY=sk-ant-…` and `LLM_BACKEND=anthropic`, then `docker compose up -d`.
For kind: put the key in `deploy/k8s/overlays/dev/secrets.env`, set `LLM_BACKEND=anthropic` in
`deploy/k8s/overlays/dev/config.env`, then re-apply (section 7).

```bash
make demo-claude CASE=05        # single seeded PR with Claude + real embeddings (in-process)
graphreview --llm anthropic --embeddings fastembed eval --modes graph_rag    # benchmark, one mode (cheaper)
make eval                       # benchmark, all three context modes → eval/results/eval-<ts>.md
make eval-offline               # harness smoke test, no API calls
```

## 9. Real Graphify extractor

```bash
GRAPHIFY_IT=1 .venv/bin/pytest tests/test_graphify_integration.py -v
```

If it passes, set `GRAPHIFY_ENABLED=true` in `.env` and in `deploy/k8s/overlays/dev/config.env`.

## 10. Live GitHub demo (creates a repo on your account — asks first)

```bash
gh extension install cli/gh-webhook
# .env: GITHUB_TOKEN=<output of `gh auth token`>   GITHUB_WEBHOOK_SECRET=<openssl rand -hex 20>
make up
make github-demo                # VISIBILITY=public CASES="01 05" make github-demo to customise
```

## 11. Other tools

```bash
make docs                       # regenerate architecture diagrams + docs/architecture.html after editing docs/diagrams/build.py
# MCP server: use the review index from Claude Code (stack must be running)
claude mcp add graphreview -- .venv/bin/graphreview mcp --repo eval/shopapp --context-url http://localhost:8001

graphreview seed-case --case 07 --path /some/checkout   # apply a seeded bug to any copy of the sample app
gh run watch                                            # follow the latest CI run
gh run list --limit 5
```
