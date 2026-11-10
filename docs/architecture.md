# Architecture

HomeGrown is two LangGraph agents and a PostgreSQL database. This page explains what
each part does and where to find it in the code.

## Data model

All of this lives in PostgreSQL (`db/*.sql`, applied in order on first start).

| Table | What it holds |
|---|---|
| `asset` | One row per reusable unit: name, summary, one-line purpose, stage, owning team, discovery scope |
| `claim` | Facts about an asset, each with a link to its `evidence` (source, ref, version, excerpt) |
| `embedding` | Vectors for each asset (fields `card`, `summary`, `purpose`), with a hash of the embedded text |
| `team`, `lob`, `person`, `asset_contributor` | Who built what, their team, line of business and manager. People are pseudonymous |
| `app_user`, `membership`, `source_acl` | Who can ask, which scopes they can discover, which sources they can read |
| `repo_state`, `unit_fingerprint` | Sync bookkeeping: which repos changed, and the Git SHAs last seen for each unit |
| `question` | Every question asked, with its route (CLI, MCP, HTTP) |

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

## Entry points

| | |
|---|---|
| `homegrown` CLI | `src/homegrown/cli.py`: `find`, `ask`, `add-user` |
| MCP server | `src/homegrown/mcp_server.py`, over stdio |
| HTTP | `src/homegrown/api.py`: webhooks and `GET /ask` |
| Sync worker | `scripts/sync_worker.py` |
| Docs-page worker | `scripts/collector_worker.py` |
