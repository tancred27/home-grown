# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Who to ask.** Each result names up to two people: its most recent committers who are in
  the company directory, with their team, their manager and their last commit. The asker
  and inactive people are skipped; with no one left it names the owning team's lead.
  Shown in the answer text, the MCP results and the CLI.
- **Directory import** (`homegrown directory import FILE.json`): a snapshot of id, name,
  team, manager, line of business, active flag, emails and logins. Emails and logins are
  stored only as hashes; commits map to people by author email, then login. People missing
  from a later snapshot are marked inactive, never deleted. Needs `db/016_directory.sql`.
- **Clarifying question** (`HOMEGROWN_JUDGE_CLARIFY`, on): when a request leaves open
  something that changes the best answer, the judge adds one question with 2 to 4 choices
  next to its results.
- **Duplicates across teams**, off by default (`HOMEGROWN_DUPLICATES_ENABLED`): a batch job
  finds units owned by different teams that do the same job, from search and model-written
  capability labels, and a judge marks pairs same, overlapping or different. "Same" pairs
  form groups that admins (`HOMEGROWN_ADMIN_USERS`) confirm or reject with
  `homegrown duplicates` or `/api/duplicates`. Needs `db/015_duplicates.sql`.
- **A code-reading crawler** (`HOMEGROWN_CRAWL_ENABLED`, bring your own model key). It
  summarises source code by area with a cheap model and splits each repo into units with
  search profiles using a stronger model. Infrastructure code, scripts and notebooks count
  as source; a secret filter runs before anything is stored or sent to a model. Needs
  `db/010_units_profiles.sql`.
- **Answer traces:** every answer returns a `question_id`; `homegrown explain <id>`,
  `GET /explain/{id}` or the MCP tool `explain` show how it was produced. Only the asker
  can read a trace.
- **Org map** dashboard (`GET /ui`, `GET /api/org-map`): everything the caller may
  discover, by line of business and stage.
- A `replay` model provider that serves recorded outputs, for development without API
  credits.
- Optional traces and metrics over OpenTelemetry (`HOMEGROWN_OTEL_ENABLED`, the `[otel]`
  extra), never carrying question text, prompts, model output or user identity. See
  `docs/observability.md`.
- A Dockerfile, a release workflow (wheel, sdist, GitHub Release and a multi-arch image on
  GHCR) and Dependabot.

### Changed
- **The judge** (`HOMEGROWN_MODEL_JUDGE=smart`, model mode on by default): one Claude Opus
  call reads the top 20 search results and picks and orders up to 3, each labelled use
  as-is or related with a one-sentence reason, or says nothing fits. A blind reviewer
  preferred its answers 16 to 3 against the earlier judge. With another provider, set
  `HOMEGROWN_JUDGE_MODEL`. `requirements` and `fit` keep the earlier judges.
- Answers say "Yes" only when something fits as-is; otherwise "Related", with results
  grouped under "Use as-is", "Related, check these" and "Not ruled out".
- **Search** ranks with BM25 keyword scoring (statistics count only what the asker can
  see), scores each component by its two best-matching vectors, and merges the lists by
  reciprocal-rank fusion. Needs `db/009_bm25.sql`; `db/013_bm25_index.sql` makes it 3–8x faster at 20,000
  components. A code-words list over crawled code exists but is off by default
  (`HOMEGROWN_CODE_WORDS_WEIGHT=0`, `db/011`–`012`).
- Simpler README; results and limits in `docs/results.md`.

### Fixed
- **The judge now sees a crawled component's capabilities.** They are stored as layer-1
  claims and the judge's card took only layers 2 and 3, so a crawled component's card was one
  sentence, sent twice (as description and purpose). The card now lists capabilities first
  and drops a purpose that repeats the description.
- With the crawler on, a crawled repo no longer shows the same code twice: a unit found
  from a package manifest points at the crawler unit covering its folder and search skips
  it. Needs `db/014_superseded.sql`.

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
