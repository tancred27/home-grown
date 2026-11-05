#!/usr/bin/env bash
# Experiment E: fetch issue/PR comments for the claim-density check.
# Usage: ./fetch.sh <target-dir>   (use a fresh, empty directory; contents are untrusted data)
#
# Uses the flat /issues/comments endpoint: 100 real comments per request, which keeps
# the whole run inside the unauthenticated 60 requests/hour limit. Set GITHUB_TOKEN for
# a larger budget (5,000/hour) and more pages.
set -u
T="${1:?target dir}"; mkdir -p "$T"
UA="org-memory-density-check/0.1"
ORG="${ORG:-grafana}"
REPOS="${REPOS:-grafana loki tempo mimir k6}"
PAGES="${PAGES:-3}"
# Token, in order of preference: $GITHUB_TOKEN, then a token file ($GH_TOKEN_FILE or
# ~/.gh_token). Keep the file outside the repo. A token with NO scopes is enough for
# public repositories and lifts the limit from 60 to 5,000 requests/hour.
TOK="${GITHUB_TOKEN:-}"
TOKFILE="${GH_TOKEN_FILE:-$HOME/.gh_token}"
if [ -z "$TOK" ] && [ -r "$TOKFILE" ]; then
  # Extract a token-shaped string rather than taking the whole file, so a file with a
  # label or surrounding prose still works.
  TOK=$(grep -oE '(ghp_|github_pat_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]+' "$TOKFILE" | head -1)
  [ -z "$TOK" ] && TOK=$(tr -d ' \t\r\n' < "$TOKFILE")
fi
AUTH=()
[ -n "$TOK" ] && AUTH=(-H "Authorization: Bearer $TOK")
echo "auth: $([ -n "$TOK" ] && echo "token (${#TOK} chars)" || echo "none — 60 req/hour")"

for r in $REPOS; do
  for p in $(seq 1 "$PAGES"); do
    f="$T/${r}_c${p}.json"
    code=$(curl -s -m 30 -w "%{http_code}" -A "$UA" \
      -H "Accept: application/vnd.github+json" "${AUTH[@]+"${AUTH[@]}"}" \
      "https://api.github.com/repos/${ORG}/${r}/issues/comments?per_page=100&page=${p}&sort=created&direction=desc" \
      -o "$f")
    echo "$r p$p http=$code n=$(jq 'if type=="array" then length else 0 end' "$f" 2>/dev/null)"
    sleep 1
  done
done
curl -s -m 15 https://api.github.com/rate_limit | jq -r '.resources.core | "budget remaining \(.remaining)/\(.limit)"'
