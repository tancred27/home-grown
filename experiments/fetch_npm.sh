#!/usr/bin/env bash
# Fetch ~1000 npm package descriptions (25 topics x 40) for retrieval experiment D.
# Usage: ./fetch_npm.sh <target-dir>
set -u
T="${1:?target dir}"; mkdir -p "$T"; cd "$T"
for t in "retry failed requests" "rate limit" "csv parser" "pdf generation" "date formatting" "feature flags" "queue jobs" "schema validation" "markdown to html" "logging structured" "cache memoize" "websocket client" "image resize" "cron scheduler" "diff compare objects" "env config loader" "pagination helper" "jwt auth" "email sending" "slugify string" "deep clone" "file upload" "i18n translation" "cli argument parsing" "http mock"; do
  f=$(echo "$t" | tr ' ' _).json
  curl -s -m 20 "https://registry.npmjs.org/-/v1/search?text=$(echo "$t" | sed 's/ /%20/g')&size=40" -o "$f"
done
ls | wc -l
