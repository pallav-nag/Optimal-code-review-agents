# PR Review Agent Pipeline

Multi-agent code review system with Graphify-powered context retrieval.

## Architecture

```
GitHub PR → Webhook → API Gateway → Redis Queue
                                        ↓
                          ┌─────────────┼─────────────┐
                     Static Analysis  LLM Review  Test Suggester
                          └─────────────┼─────────────┘
                                        ↓
                                   Aggregator
                                        ↓
                                 Critic / Scorer
                                        ↓
                                GitHub PR Comment
```

Each agent queries `graph.json` via Graphify MCP — never loads full files.

## Quick Start

```bash
# 1. Build graphs for your repos
./scripts/build_graphs.sh /path/to/repo

# 2. Start the stack
docker compose up

# 3. Configure GitHub webhook
#    URL: https://your-domain/webhook
#    Events: pull_request
#    Secret: set in .env
```

## Token Savings

| Approach          | Context per review | Avg tokens |
|-------------------|--------------------|------------|
| Full file loading | All changed files  | ~40k       |
| Graphify queries  | Subgraph only      | ~6k        |

~85% reduction. Agents call `graphify query "what depends on <changed_fn>?"` instead of reading files.
