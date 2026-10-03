.PHONY: install test lint demo demo-claude eval eval-offline up down logs images stack-index stack-demo github-demo k8s-up k8s-down

PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

install:            ## create .venv and install everything (editable)
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e ".[dev,rag,graph,static,mcp]"
	@echo "✓ installed — activate with: source $(VENV)/bin/activate"

test:               ## unit + in-process end-to-end tests (no network, no keys)
	$(BIN)/pytest -q

lint:
	$(BIN)/ruff check src tests

demo:               ## offline: seeded bug → full pipeline, rule-based LLM + hash embeddings
	$(BIN)/graphreview --llm fake --embeddings hash demo --case $(or $(CASE),01)

demo-claude:        ## same, with Claude + real embeddings (needs ANTHROPIC_API_KEY)
	$(BIN)/graphreview --llm anthropic --embeddings fastembed demo --case $(or $(CASE),05)

eval:               ## seeded-bug benchmark with Claude, all three context modes (costs API credits)
	$(BIN)/graphreview --llm anthropic --embeddings fastembed eval --modes graph_rag,full_files,diff_only

eval-offline:       ## harness smoke test with the rule-based fake
	$(BIN)/graphreview --llm fake --embeddings hash eval --modes graph_rag --out eval/results/offline

up:                 ## full stack: Kafka, Redis, Qdrant, services, Prometheus, Grafana
	@test -f .env || cp .env.example .env
	docker compose up --build -d
	@echo "✓ dashboard http://localhost:8080 · grafana http://localhost:3000"

down:
	docker compose down

logs:
	docker compose logs -f --tail=50 gateway agent-reviewer aggregator critic publisher

stack-index:        ## index the sample repo (eval/fixtures/shopapp) into the running stack
	docker compose run --rm -v $(PWD)/eval:/demo/eval:ro indexer index --repo eval/shopapp --path /demo/eval/fixtures/shopapp

stack-demo:         ## send a seeded-bug PR through the running stack (Kafka → agents → critic) and print the review
	$(BIN)/graphreview submit --case $(or $(CASE),01) --gateway $(or $(GATEWAY),http://localhost:8080)

images:
	docker build --target core -t graphreview-core:dev .
	docker build --target rag -t graphreview-rag:dev .

github-demo:        ## real repo + seeded PRs on your GitHub, reviewed by the local stack (asks before creating)
	./scripts/github-demo.sh

k8s-up:             ## kind cluster + Strimzi + KEDA + the app (dev overlay)
	./scripts/kind-up.sh

k8s-down:
	kind delete cluster --name graphreview
