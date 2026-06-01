#!/usr/bin/env bash
# Build / update Graphify knowledge graphs for one or more repos.
# Run in CI after merge to main, or manually for initial setup.
#
# Usage:
#   ./scripts/build_graphs.sh /path/to/repos/owner/repo
#   ./scripts/build_graphs.sh /path/to/repos  # builds all subdirs
#
set -euo pipefail

REPOS_BASE="${REPOS_PATH:-/repos}"

build_one() {
    local repo_path="$1"
    local name
    name=$(basename "$(dirname "$repo_path")")/$(basename "$repo_path")

    echo "=== Building graph: $name ==="

    # Install graphify if not present
    if ! command -v graphify &>/dev/null; then
        pip install graphifyy --quiet
    fi

    cd "$repo_path"

    # Build / update graph (AST only = no API cost for code)
    # Docs/PDFs need ANTHROPIC_API_KEY — skip in CI unless set
    if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
        graphify extract . --update --backend claude
    else
        graphify extract . --update --no-cluster 2>/dev/null || \
        graphify . --no-viz
    fi

    echo "✓ Graph ready at $repo_path/graphify-out/graph.json"
}

if [[ -f "$1/graphify-out/graph.json" ]] || [[ -d "$1/.git" ]]; then
    # Single repo
    build_one "$1"
else
    # Directory of repos — build all
    find "$1" -maxdepth 2 -name ".git" -type d | while read -r gitdir; do
        build_one "$(dirname "$gitdir")"
    done
fi

echo ""
echo "All graphs built. Start MCP server with:"
echo "  python -m graphify.serve $REPOS_BASE"
