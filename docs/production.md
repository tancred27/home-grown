# Running in production

HomeGrown hasn't run inside a company yet. This page lists what a deployment needs and
what is still missing, so you can decide what to add first.

## What you need

- **PostgreSQL 16+ with pgvector 0.8+.** Apply `db/*.sql` in order, as the schema owner.
  Then change the passwords of `homegrown` and `homegrown_app` from the development
  defaults. Answer traffic must use the `homegrown_app` role: it can't bypass row-level
  security.
- **One or more sync workers:** `scripts/sync_worker.py <org>`. See [github.md](github.md).
- **The API**, if you use webhooks or HTTP questions: `uvicorn homegrown.api:app`, behind
  TLS.
- **An LLM provider** for model verdicts, query rewriting and purpose lines. Expect two
  model calls per question: the rewrite, and the judge (Claude Opus by default, reading 20
  results).
- **Your directory**, for who to ask: `homegrown directory import people.json` with a
  snapshot of id, name, team, manager, line of business, active flag, emails and logins.
  Run it on a schedule. Emails and logins are stored only as hashes; people missing from a
  later snapshot are marked inactive, never deleted.
- **Monitoring**, optional: traces and metrics over OpenTelemetry, with no question
  text or code in them. See [observability.md](observability.md).

## Sizing, from our measurements

- **Search.** At 300k vectors (~100k assets), with the HNSW index: about 0.1 s per search
  and ~80 searches/s on one laptop-class database. The index is used automatically
  above `HOMEGROWN_HNSW_AUTO_THRESHOLD`.
- **Intake.** Replaying one hour of all public GitHub pushes (111k pushes to default
  branches across 53k repos), the intake and scheduler kept every change under 10
  minutes, with headroom of 11× or more.
- **Processing.** Cost depends on how many units change, not on commit count. A day of
  changes on kubernetes, vscode and rust together took one worker about 3 minutes,
  measured with the LLM off. Each changed unit adds a purpose-line model call when the
  README or manifest changed.
- **First index.** Use local mirrors for large orgs. Fetching files one by one over the
  API runs at about one file per second.

Details: `experiments/sync/RESULTS.md` and `experiments/scale/RESULTS.md`.

## Not built yet

- **Sign-in.** Users come from `homegrown add-user`, and the API trusts the
  `X-Homegrown-User` header. Put the API behind your identity provider and map its
  identity to `app_user`, or replace `resolve_user` in `api.py` and `mcp_server.py`.
- **Permission sync.** Team membership (`membership`) and source access (`source_acl`)
  aren't synced from GitHub teams or your directory yet. Today, sync assigns one team
  per repo.
- **A directory connector.** There is no SCIM, Okta or Entra connector yet; something
  must write the import file. Teams loaded from a directory have no lead yet, so a result
  with no recent committer in the directory names nobody.
- **Caching.** Model calls are not cached across questions.

Duplicates across teams is off by default (`HOMEGROWN_DUPLICATES_ENABLED`). Its judge is
one Opus call per component, about 3.6M input tokens per 1,000 components, so plan its
first run.

If you run HomeGrown somewhere real, we'd like to hear how it goes. Please open an issue.
