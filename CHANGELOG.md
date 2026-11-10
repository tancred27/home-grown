# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- A code-reading crawler (`HOMEGROWN_CRAWL_ENABLED`, bring your own model key). Sync
  passes read source code, summarise it by area with a cheap model, and split each repo
  into units with search profiles using a stronger model. Units are child assets of their
  repo, their capabilities are claims backed by code-area evidence, and a secret filter
  runs before anything is stored or sent to a model. Needs `db/010_units_profiles.sql`.
- Answer traces: every answer returns a `question_id`, and `homegrown explain <id>`,
  `GET /explain/{id}` or the MCP tool `explain` show how it was produced, step by step.
  Only the asker can read a trace. `scripts/explain_report.py` shows where expected
  answers were lost.
- A `replay` model provider that serves recorded outputs, for development without API
  credits.
- Optional traces and metrics over OpenTelemetry (`HOMEGROWN_OTEL_ENABLED`, the `[otel]`
  extra). They record counts, durations, labels and model usage, never question text,
  prompts, model output or user identity. A `docker compose --profile observability`
  stack runs a collector, Tempo, Prometheus and a Grafana dashboard, keeping data for
  30 days. See `docs/observability.md`.
- A Dockerfile, a release workflow (wheel, sdist and a GitHub Release, plus a
  multi-arch image on GHCR) and Dependabot.

### Changed
- Search now ranks with BM25 keyword scoring, scores each component by its two
  best-matching vectors, and merges the two lists by reciprocal-rank fusion. At 5,000
  components the right one is in the top 5 for 85% of test questions (was 79%). BM25
  statistics count only what the asker can see. Needs `db/009_bm25.sql`; the previous
  behaviour stays available through `HOMEGROWN_LEXICAL`, `HOMEGROWN_DENSE_POOL` and
  `HOMEGROWN_FUSION`.
- In model mode, answers say "Yes" only when something fits as-is. When the best match
  would need extending, the answer is "Related", and results are grouped under
  "Use as-is", "Related, check these" and "Not ruled out".
- Simpler README with images; detailed results moved to `docs/results.md`.

## [0.1.0] - 2026-11-08

First public release, as a research prototype.

### Added
- Collection agent (LangGraph): finds reusable units from manifests and records what
  each unit is, what its tests say it does, a one-line purpose, contributors, team,
  line of business, manager and an inferred stage. Every claim links to its evidence.
- State-based sync: a poller and an optional GitHub webhook mark repos as changed; a
  scheduler with leases hands them to workers; Git fingerprints skip units that didn't
  change. Works from the GitHub API or local mirrors.
- Answering agent: query rewriting, keyword and vector search, a model fit judge that
  fails safe to `unknown`, and up to five results with owner, stage and access.
- Permissions enforced by PostgreSQL row-level security, including indexed keyword
  search through `fts_hits()`, with a leakage test over every user and asset.
- Automatic switch from exact vector search to an HNSW index as the corpus grows.
- CLI (`homegrown find`, `ask`, `add-user`), MCP server and HTTP API.
- LLM providers: Anthropic, Groq, Gemini, and any OpenAI-compatible server.
- Optional owner approval step (`HOMEGROWN_OWNER_GATE`).
- Evaluation and benchmark scripts with recorded results in `experiments/`.
