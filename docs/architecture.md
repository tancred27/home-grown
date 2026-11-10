# Architecture

HomeGrown is two LangGraph agents and a PostgreSQL database. This page explains what
each part does and where to find it in the code.

## Data model

All of this lives in PostgreSQL (`db/*.sql`, applied in order on first start).

| Table | What it holds |
|---|---|
| `asset` | One row per reusable unit: name, summary, one-line purpose, stage, owning team, discovery scope |
| `claim` | Facts about an asset, each with a link to its `evidence` (source, ref, version, excerpt) |
| `embedding` | Vectors for each asset (fields `card`, `summary`, `purpose`; for crawled units also `capabilities`, `search_terms`, `question_N`, `area_N`), with the embedded text and its hash |
| `area`, `profile` | Crawled code areas (summary, files, content hash) and each unit's search profile (purpose, capabilities, search terms, example questions) |
| `team`, `lob`, `person`, `asset_contributor` | Who built what, their team, line of business and manager. People are pseudonymous |
| `app_user`, `membership`, `source_acl` | Who can ask, which scopes they can discover, which sources they can read |
| `repo_state`, `unit_fingerprint` | Sync bookkeeping: which repos changed, and the Git SHAs last seen for each unit |
| `question`, `query_trace` | Every question asked, with its route (CLI, MCP, HTTP), and the trace of how its answer was produced |

Claims come in three layers:
1. **What it is.** From manifests (`package.json`, `go.mod`, `pyproject.toml`, ...) and the
   repo description.
2. **What it does.** From test names. A test describes one behaviour, and it stays true
   or the build breaks.
3. **How it went.** From discussions, docs pages and commit messages.

## Permissions

Every read runs as the asking user. `db.as_user()` sets `app.user_id` for one
transaction, and the row-level security policies in `db/002_rls.sql` filter every table
on it. The application role is not a superuser and can't bypass row-level security. With
no user set, the policies return nothing.

There are two levels of visibility:
- **Discover:** you can see that an asset exists, what it does and who to ask. That
  needs membership in the asset's `discovery_scope`. Scope `org-wide` means everyone.
- **Read:** you can see detail claims marked sensitive. That needs a `source_acl`
  entry for the asset's source.

Keyword search goes through `bm25_hits()` (`db/009_bm25.sql`), or `fts_hits()` (`db/008_fts.sql`) with `lexical = ts_rank`. Postgres can't use a full-text
index on a table with row-level security, so this function runs with elevated rights,
applies the same visibility rules itself, and is owned by a role that can do nothing
else. Results are joined back to `asset` under row-level security, which checks them a
second time. `tests/test_leakage.py` checks both paths for every user and every asset.

## The collection agent

`src/homegrown/collector/`

**Intake.** A push to a repo's default branch only marks the repo as changed
(`state.mark_dirty`). There are two ways it happens:
- `poller.poll_org` lists an org's repos sorted by last push, one API call per 100 repos;
- the GitHub webhook does the same for one repo.

**Scheduling.** `state.claim_due` hands a changed repo to a worker once it has been quiet
for `sync_quiet_s`, or has waited `sync_max_wait_s`. A lease stops two workers from taking
the same repo. Failures back off.

**A sync pass.** `sync.Syncer`:
1. Reads the repo's current tree (`source.GitHubSource` or `source.LocalGitSource`).
   One call returns every file's SHA.
2. Finds units (folders with a manifest) and compares each unit's folder SHA and its
   manifest, README and test file SHAs with the last pass. Units with no changes are
   skipped.
3. Downloads only the files those units need into a small cache, and runs the collector
   graph on them.

Because it compares states instead of replaying commits, merges, rebases, force-pushes
and reverts need no special handling.

**The collector graph.** `collector/graph.py`, one run per event:

```
triage -> locate -> decide -> extract -> update_map -> attribute -> assess_stage
       -> validate -> owner_gate -> apply
```

- `triage` drops bots, dependency bumps and lockfile-only changes without calling a model.
- `extract` writes the three claim layers.
- `update_map` rewrites the purpose line only when the README or manifest changed.
- `attribute` and `assess_stage` work out who built the unit and its stage (dev, beta,
  prod or retired), always marked unconfirmed.
- `validate` drops claims without evidence and claims that mention people's personal
  matters.
- `owner_gate` is optional and off by default. When on, it pauses with `interrupt()`
  to ask the owner about visibility and deprecation.
- `apply` writes everything in one transaction and re-embeds only text that changed.

The agent never deletes anything and never marks anything deprecated by itself.

**Daily jobs.** Contributors and stage change slowly and cost API calls, so for existing
assets a daily pass refreshes them. `usage.refresh` reads dependency manifests across
repos: an internal library used by two other teams is in production use, whatever its
release history says.

**Docs pages.** Confluence and Notion webhooks (`api.py`) put page events in the `event`
queue, which `scripts/collector_worker.py` drains. An LLM decides whether a page records
a solution, decision or pitfall worth keeping (`pages.py`).

## The code-reading crawler

`src/homegrown/collector/crawl.py`, run by the sync pass when `HOMEGROWN_CRAWL_ENABLED` is
on. Code is the source of truth; the README is one input.

1. **Pick files** by rules (`extract/code.py`): source files only; no tests, vendored or
   build output, configs, or generated or minified files (judged from content, not size).
   A fingerprint of the repo's source files skips repos whose code did not change.
2. **Extract without a model:** declaration lines and doc comments, then the secret
   filter. Files are grouped into areas of at most three path levels.
3. **Summarise each changed area** with a cheap model. Summaries are cached by the hash of
   the area's text.
4. **Compile units** with a stronger model from the README, the folder list, the area
   summaries and the previous units. A unit has standalone reuse value; a small feature is
   detail inside its unit. Every area belongs to exactly one unit. Skipped when the input
   is unchanged; unit ids are kept across crawls by area overlap.
5. **Write** each unit as an asset whose `parent_id` is the repo's asset, with its profile,
   its capabilities as claims (evidence: the code areas at a commit), and its vectors.
   Capabilities the code no longer supports are closed, not deleted.

Every model call fails safe: the previous summary or units stay.

## The answering agent

`src/homegrown/search.py` and `src/homegrown/answer/graph.py`

1. **Rewrite** (model mode). The request is restated as the purpose line of a component
   that would satisfy it, in the vocabulary component descriptions use.
2. **Search.** Keyword search and vector search run together, each about 200 deep, and are
   merged by reciprocal-rank fusion (each list adds 1/(60 + rank)).
   - Keyword search is BM25 (`bm25_hits`, `db/009_bm25.sql`): an asset's text plus the claims
     the caller may read, claims at half weight. Corpus statistics count only assets the caller
     can discover, so hidden assets never shift anyone's ranking.
   - Each asset has several vectors (card, purpose, profile fields) and scores by the mean of its
     two best, so one lucky field can't carry it.
   - The older choices (`ts_rank`, best single field, interleaving) remain as settings.
   - Below `hnsw_auto_threshold` vectors, vector search is an exact scan over the rows the
     user can see.
   - Above it, an HNSW index fetches a candidate pool with iterative scan, and the pool
     is ranked exactly.
3. **Judge.** One model call reads the top `verdict_shortlist` candidates against the
   request and labels each `use_as_is`, `extend`, `not_a_fit` or `unknown`. On any error,
   everything is `unknown`.
4. **Compose.** Fits come first, then candidates the judge couldn't rule out, up to
   `answer_results`. Not-a-fit candidates are never shown. Each result gets owner, team,
   line of business, manager, stage and access.

In rules mode (no model), verdicts come from the evidence alone, and the answer says
"possibly" instead of "yes".

## Explaining an answer

Every answer returns a `question_id`, and `homegrown explain <id>` (or `GET /explain/{id}`,
or the MCP tool `explain`) shows how it was produced:

- the request, the model's rewrite and the keyword query;
- for each candidate: its rank in keyword search with the BM25 score broken down by term
  (term frequency in its text and claims, document frequency, idf, contribution); its rank
  in vector search with its two best-matching fields, their similarity and text; and its
  fused rank and score;
- the judge's model, prompt version, shortlist, and each verdict with its reason;
- which candidates were shown, in what order, and which were held back;
- where each candidate came from: for a crawled unit, its repo, profile model and commit,
  and the code areas and files behind it;
- the time each step took.

Traces live in `query_trace` under row-level security: only the person who asked can read
theirs, and they contain only assets that person could already discover. Telemetry never
carries any of it. `scripts/explain_report.py` reads traces to show where expected answers
were lost (not indexed, not retrieved, ranked too low, judged not a fit, not shown).

## Entry points

| | |
|---|---|
| `homegrown` CLI | `src/homegrown/cli.py`: `find`, `ask`, `explain`, `add-user` |
| MCP server | `src/homegrown/mcp_server.py`, over stdio |
| HTTP | `src/homegrown/api.py`: webhooks, `GET /ask` and `GET /explain/{id}` |
| Sync worker | `scripts/sync_worker.py` |
| Docs-page worker | `scripts/collector_worker.py` |
