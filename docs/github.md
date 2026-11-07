# Connecting GitHub

HomeGrown reads repositories through the GitHub REST API, or from local mirrors for very
large orgs. It only reads.

## Token

Create a fine-grained personal access token, or a GitHub App installation token, with:
- **Repository access:** the repos you want indexed;
- **Permissions:** Contents (read), Metadata (read). Add Checks (read) and Commit
  statuses (read) if you want CI status shown on assets.

Save the token in a file outside the repo and point to it:

```bash
HOMEGROWN_GITHUB_TOKEN_FILE=/secure/path/github-token.txt
```

The token is read from that file when needed, and it is never logged.

## First index

```bash
python -I scripts/sync_worker.py <org> --once
```

This lists the org's repos, marks each one as changed, processes every repo that is due,
and exits. Repos that haven't been pushed to for a couple of minutes are due straight
away. Run it again for any repos still waiting.

Over the API, files are fetched one at a time, about one per second, and only manifests,
READMEs and test files are read. A small org takes a few minutes (11 pallets repos: under
5 minutes). A first index of a very large repo can take much longer, so for big orgs
use local mirrors (below).

## Keeping up

Run the worker without `--once`:

```bash
python -I scripts/sync_worker.py <org> [<org> ...]
```

It polls every `HOMEGROWN_SYNC_POLL_S` seconds, processes changed repos, and runs the
daily refresh of contributors and stage. You can run several workers at once (on one
machine or many): each repo is leased to one worker at a time.

A repo is processed `HOMEGROWN_SYNC_QUIET_S` after its last push, or at most
`HOMEGROWN_SYNC_MAX_WAIT_S` after its first unprocessed push. A busy repo gets one pass
every few minutes, not one per commit.

## Webhook (optional, for faster updates)

Polling alone is enough. A webhook just makes changes show up sooner.

1. Run the API: `uvicorn homegrown.api:app --host 0.0.0.0 --port 8080`
2. Set `HOMEGROWN_GITHUB_WEBHOOK_SECRET` to a long random string.
3. In the org's settings, add a webhook:
   - **Payload URL:** `https://<your-host>/hooks/github`
   - **Content type:** `application/json`
   - **Secret:** the same string
   - **Events:** just the push event

Every delivery is checked against the secret. Only pushes to a repo's default branch
count; other branches are ignored.

## Very large orgs: local mirrors

To avoid API rate limits entirely, keep bare mirrors (`git clone --mirror`) under one
folder, laid out as `<dir>/<org>/<repo>.git`, and keep them fetched:

```bash
python -I scripts/sync_worker.py <org> --mirror-dir /data/mirrors
```

The worker then reads trees and files from the mirrors with `git` instead of the API.
Polling for changed repos still uses the API, one call per 100 repos.
