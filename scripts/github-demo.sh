#!/usr/bin/env bash
# Live GitHub demo: real PRs on a real repo, reviewed by the local stack, comments posted back.
#
#   1. creates <you>/shopapp-graphreview-demo from eval/fixtures/shopapp (private by default)
#   2. forwards that repo's webhooks to the local gateway (`gh webhook forward`, no public tunnel)
#   3. indexes the repo through the pipeline (indexer clones it from GitHub)
#   4. opens one PR per seeded-bug case → GraphReview posts a review with inline comments
#
# Prereqs: `make up` running with GITHUB_TOKEN + GITHUB_WEBHOOK_SECRET in .env,
#          `gh auth login`, `gh extension install cli/gh-webhook`, graphreview CLI on PATH.
# Env: REPO_NAME, VISIBILITY=private|public, CASES="01 03 05 07", GATEWAY=http://localhost:8080
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_NAME=${REPO_NAME:-shopapp-graphreview-demo}
VISIBILITY=${VISIBILITY:-private}
CASES=${CASES:-"01 03 05 07"}
GATEWAY=${GATEWAY:-http://localhost:8080}

SECRET=$(grep -E '^GITHUB_WEBHOOK_SECRET=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | sed 's/[[:space:]]*#.*//')
[[ -n "$SECRET" ]] || { echo "Set GITHUB_WEBHOOK_SECRET in .env and restart the stack (make up)"; exit 1; }
grep -qE '^GITHUB_TOKEN=.+' "$ROOT/.env" || { echo "Set GITHUB_TOKEN in .env (e.g. \$(gh auth token)) and restart"; exit 1; }
gh extension list | grep -q gh-webhook || { echo "Run: gh extension install cli/gh-webhook"; exit 1; }
curl -sf "$GATEWAY/healthz" >/dev/null || { echo "Gateway not reachable at $GATEWAY (make up)"; exit 1; }

OWNER=$(gh api user -q .login)
FULL="$OWNER/$REPO_NAME"
read -r -p "Create $VISIBILITY repo $FULL and open PRs for cases [$CASES]? [y/N] " ok
[[ "$ok" == [yY]* ]] || exit 0

WORK=$(mktemp -d)
cp -R "$ROOT/eval/fixtures/shopapp/." "$WORK/"
cd "$WORK"
git init -q -b main && git add . && git commit -qm "Import shopapp"
gh repo create "$FULL" "--$VISIBILITY" --source . --push --description "Demo target for GraphReview"

echo "▶ forwarding webhooks → $GATEWAY/webhook"
gh webhook forward --repo="$FULL" --events=pull_request,push --url="$GATEWAY/webhook" --secret="$SECRET" &
FWD=$!
trap 'kill $FWD 2>/dev/null || true' EXIT
sleep 5

echo "▶ indexing $FULL"
curl -sf -XPOST "$GATEWAY/api/repos/index" -H 'content-type: application/json' -d "{\"repo\":\"$FULL\"}" >/dev/null
for _ in $(seq 1 60); do
  curl -sf "$GATEWAY/api/repos/$FULL" >/dev/null && break
  sleep 3
done
curl -sf "$GATEWAY/api/repos/$FULL" || { echo "indexing did not finish — check: docker compose logs indexer"; exit 1; }
echo

for c in $CASES; do
  git checkout -q main && git checkout -qb "demo/$c"
  GR="$ROOT/.venv/bin/graphreview"; [[ -x "$GR" ]] || GR=graphreview
  meta=$(cd "$ROOT" && "$GR" seed-case --case "$c" --path "$WORK")
  title=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["title"])' "$meta")
  body=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["body"])' "$meta")
  git commit -qam "$title" && git push -qu origin "demo/$c"
  gh pr create --base main --head "demo/$c" --title "$title" --body "$body"
done

echo "✓ PRs: https://github.com/$FULL/pulls · live pipeline: $GATEWAY"
echo "  keep this running while reviews post (Ctrl+C stops webhook forwarding)"
wait $FWD
