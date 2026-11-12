# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Code words: search also looks in each crawled unit's own code (declarations and doc
  comments, identifiers split into words) as a third ranked list, fused with keyword and
  vector search. On 196 fresh test questions it put the right component in the top 5
  8 to 11 points more often. Anyone who can find a unit can be matched through its code
  words; which words matched is shown only to people who can read its source. Needs
  `db/011_code_words.sql`; `HOMEGROWN_CODE_WORDS_WEIGHT=0` turns it off. Repos crawled
  before this get code words on their next change, or on a forced re-crawl.
  `db/012_code_terms.sql` adds the inverted index that keeps it fast (about 30x faster
  than 011 alone at 5,000 units); apply it after 011.
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
- The requirements judge notes when a result is built for one platform the request did not
  name ("built for Amazon Bedrock only"); such a result is never listed as use-as-is. It marks a
  result as "is one" only when that is its main purpose, and "has one inside" when it is a part.
- The requirements judge has a third kind state, "has one inside": a larger unit that builds,
  sets up or deploys the kind of thing asked for as one part of it. It is listed as related
  ("not a standalone X, but includes one"), below results of that kind and above "not ruled out".
- The model judge is on by default (`HOMEGROWN_VERDICT_MODE=model`) and pinned to Sonnet
  (`HOMEGROWN_JUDGE_MODEL=claude-sonnet-5-5`). Without a working model it fails safe and
  results keep search order; `rules` mode needs no model. With another provider, set
  `HOMEGROWN_JUDGE_MODEL` to a model that provider serves.
- A new model judge (`HOMEGROWN_MODEL_JUDGE=requirements`, the default in model mode). One
  call splits the request into the kind of unit asked for and the requirements it states,
  and marks each result as showing, contradicting or not mentioning each one, with a quote
  from its card. Code then orders the results: right kind first, then most requirements
  shown, then nothing contradicted. "Use as-is" means the kind and every requirement are
  shown. A result whose card doesn't settle the kind but shows a requirement is listed
  under "Not ruled out". Answers show 3 results, up to 5 when results tie. Each result's
  "why" names what its card shows, contradicts and leaves unstated. On the 89-question
  goal benchmark the best option came first for 51 of 63 answerable questions (37 in
  search order). `HOMEGROWN_MODEL_JUDGE=fit` keeps the previous judge.
- The crawler reads more of a repo. Infrastructure code, scripts and notebooks (Terraform,
  Bicep, PowerShell, shell, Rego, SQL, R, CUE, Sentinel, Jupyter) and deployment templates
  (CloudFormation, SAM, ARM, GitHub Action files) count as source, keeping their resource,
  variable, parameter and function lines; tests, docs, fixtures and vendored code are still
  skipped. A repo with no code outside `examples/`, `samples/`, `scripts/` or `tools/` is read
  from those folders. Licence header blocks no longer fill the extracted text. The unit
  compiler reads the whole README (`HOMEGROWN_CRAWL_README_CHARS`, 0 = no cap; was 1,400
  characters). Areas change their content hash, so each crawled repo is summarised again on
  its next pass.
- Code words are off by default (`HOMEGROWN_CODE_WORDS_WEIGHT=0`): on a goal-level benchmark
  they never beat profile search. Set the weight to 1 to turn them back on.
- Keyword search is indexed: `db/013_bm25_index.sql` keeps (document, word, count) postings
  and per-asset claim lengths with triggers, so a search reads only its words' postings.
  Results are identical to before (`tests/test_bm25_index.py`); at 20,000 assets it is 3-8x
  faster. Writes cost a little more. Apply after 012.
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
