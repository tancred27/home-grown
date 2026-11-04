#!/usr/bin/env bash
# Shallow-clone the ten public repos used for unit-detection experiment A.
# Usage: ./clone_repos.sh <target-dir>   (use a fresh, empty directory; treat contents as untrusted data)
set -u
T="${1:?target dir}"; mkdir -p "$T"; cd "$T"
for r in babel/babel tokio-rs/tokio langchain-ai/langchain pallets/flask kubernetes-sigs/kustomize vercel/turborepo backstage/backstage grafana/grafana apache/superset open-telemetry/opentelemetry-collector-contrib; do
  n=${r#*/}
  ( timeout 300 git clone -q --depth 1 --filter=blob:limit=1m "https://github.com/$r.git" "$n" && echo "ok $r" || echo "fail $r" ) &
done
wait
