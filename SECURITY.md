# Security policy

HomeGrown decides who may learn what about other teams' code, so permission bugs are
security bugs. Thank you for reporting them responsibly.

## Reporting a vulnerability

Please **don't open a public issue.** Report it privately through
[GitHub's private vulnerability reporting](https://github.com/tancred27/home-grown/security/advisories/new)
for this repository.

Include what you found, how to reproduce it, and what an attacker could learn or do. We
aim to acknowledge reports within a few days and to agree on a disclosure date with you.

## What counts

Especially:
- any way for a user to discover an asset outside their discovery scope, or to see
  sensitive claims without source access, through search, `fts_hits()`, the API, the MCP
  server or the CLI;
- running the answer path with a role that bypasses row-level security;
- webhook handlers accepting unsigned or wrongly signed deliveries;
- text from an indexed repo or page causing an action (prompt injection that does more
  than change a verdict to `unknown`);
- secrets (tokens, API keys) being logged or stored.

## Supported versions

HomeGrown is a prototype. Only the latest commit on `main` gets fixes.

## Running it safely

- Change the default database passwords (`docker-compose.yml`, `db/002_rls.sql`) before
  exposing the database.
- Use a GitHub token with read-only access.
- Don't send private code to free tiers of hosted LLM providers. See
  [docs/configuration.md](docs/configuration.md).
- The API's `X-Homegrown-User` header is not authentication. Put the API behind your
  identity provider. See [docs/production.md](docs/production.md).
