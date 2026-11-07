# HomeGrown

**Has someone in my org already built this?**

HomeGrown keeps track of what your organization has already built, so engineers and
coding agents check before building it again. Ask in plain language. You get back
the matching components, who built them, their team and manager, whether each one is in
development or production, and whether you can read the code or need to ask for access.

It works across private repositories. In most companies a developer can only read their
own team's repos, and so can their coding agent, so neither can find what another team
already built. HomeGrown can tell you that something exists, what it does and who
to ask, without showing you code you aren't allowed to read.

> **Status: research prototype.** HomeGrown works end to end and has been measured
> on real public repositories, but it hasn't run inside a real company yet. See
> [Status](#status) before you depend on it.

## How it works

Two agents, built with [LangGraph](https://github.com/langchain-ai/langgraph), share one
PostgreSQL database:

- **The collection agent** keeps an up-to-date record of every reusable unit in your repos
  (packages, services, modules). When a repo's default branch changes, it reads the
  current state of the repo, finds the units that changed, and records what each unit
  is, what its tests say it does, a one-line purpose, who built it and its likely stage.
  Every stored claim points to the evidence it came from.
- **The answering agent** takes a question, runs keyword and vector search together,
  and has a model judge each candidate against the request. It returns up to five
  results, best fits first. Anything judged not a fit is never shown.

Permissions are enforced by the database. Every search runs as the person asking,
under PostgreSQL row-level security, so no code path can return something that person
isn't allowed to discover.

```
  GitHub push ─► mark repo changed ─► sync worker ─► collection agent ─► PostgreSQL
  (webhook or                          (reads current       (units, claims,    + pgvector
   poller)                              state, not commits)  people, stage)       │
                                                                                  │ row-level
  developer / coding agent ─► CLI, MCP or HTTP ─► answering agent ◄───────────────┘ security
```

More detail: [docs/architecture.md](docs/architecture.md).

## Example

This is the output from indexing the public [pallets](https://github.com/pallets)
organization into an empty database. People are pseudonymous by design.

```
$ homegrown ask "has anyone built a way to escape HTML safely in templates?"
Yes - this has been done here.

Top 2 results (2 judged a fit):

* MarkupSafe  [use_as_is]  stage: prod (inferred, 75% confidence, unconfirmed)
  team: team-markupsafe  |  LOB: Platform Engineering  |  manager: Lead, team-markupsafe
  built by Engineer 2cebd2 (team-jinja, reports to Lead, team-jinja, 92 commits, ...)
  why: MarkupSafe provides functions to escape untrusted strings for safe HTML insertion.
  access: discoverable only: request access to github:markupsafe from Lead, team-markupsafe
  stage basis: stable release 3.0.4 (major version 3); 10 stable release(s)

* Jinja2  [use_as_is]  stage: prod (inferred, 75% confidence, unconfirmed)
  ...
```

## Features

- **Answers across team boundaries.** Teams can make a component discoverable to the
  whole org while its code stays private.
- **Evidence for every claim.** Claims come from manifests, test names, READMEs,
  docs pages and discussions, and each one links back to its source.
- **A judge that can say no.** A model reads each candidate against the request and
  labels it use as-is, extend, not a fit, or unknown. If the model is down, every
  verdict is `unknown`, so it never sends anyone to the wrong place.
- **Keeps up with busy orgs.** Sync is based on repo state, not individual commits, so
  a burst of 100 pushes costs one pass. Only the units that changed are reprocessed.
- **Three ways to ask:** a CLI, an MCP server for coding agents (Claude Code, Cursor and
  others), and an HTTP API.
- **Any LLM provider:** Anthropic, Groq, Gemini, or any OpenAI-compatible server such as
  Ollama. Search and embeddings run locally with no API key.

## Tech stack

Python 3.12+ · LangGraph · PostgreSQL 16 + pgvector 0.8 · fastembed
(`BAAI/bge-base-en-v1.5`, runs on CPU) · FastAPI · MCP Python SDK

## Quick start

You need Docker, Python 3.12+ and Git.

```bash
git clone https://github.com/tancred27/home-grown.git && cd home-grown
cp .env.example .env

docker compose up -d                 # PostgreSQL + pgvector; the schema is applied on first start
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Optionally, pick an LLM in `.env`. Without one, HomeGrown runs in rules mode: search
works, and verdicts come from the evidence alone, so it answers "possibly" instead of
"yes". Do this before the first sync, because a unit's one-line purpose is written when
the unit is first indexed (and again when its README or manifest changes).

```bash
HOMEGROWN_LLM_PROVIDER=groq                   # or anthropic, gemini, local
HOMEGROWN_VERDICT_MODE=model
HOMEGROWN_GROQ_KEY_FILE=/path/to/groq-key.txt # or export GROQ_API_KEY in your shell
```

Point it at a GitHub organization. A token raises the API rate limit, and it only needs
read access to the repos you want indexed. Put the token in a file and reference that
file:

```bash
echo "HOMEGROWN_GITHUB_TOKEN_FILE=/path/to/token.txt" >> .env
python -I scripts/sync_worker.py pallets --once     # index the org once, then exit
```

Add yourself and ask:

```bash
homegrown add-user alice
export HOMEGROWN_USER=alice
homegrown find "sign data so I can tell if it was tampered with"
homegrown ask  "has anyone built a way to escape HTML safely in templates?"
```

All settings are in [docs/configuration.md](docs/configuration.md).

## Use it from a coding agent

```bash
claude mcp add homegrown -e HOMEGROWN_USER=alice -- /path/to/.venv/bin/python -m homegrown.mcp_server
```

The MCP server exposes `find_existing`, `ask`, `get_asset` and `report_reuse_outcome`.
Any MCP-capable agent can use the same command over stdio.

## Running it for an org

Run `scripts/sync_worker.py <org>` without `--once` and it keeps polling. You can run
several workers side by side, since a lease stops two workers from processing the same
repo. Add the GitHub webhook (`POST /hooks/github` on `uvicorn homegrown.api:app`) for
faster updates. See [docs/github.md](docs/github.md) and
[docs/production.md](docs/production.md).

## Results

Every number below comes from a script in `scripts/` and a write-up in `experiments/`.
Where it mattered, choices were made on a tuning set and confirmed once on a held-out
test set.

| What | Result | Source |
|---|---|---|
| Finding the right component (51 held-out questions, 109 assets) | right one in the top 5: **94%** | `experiments/scale/RESULTS.md` |
| Same, with ~9,600 assets | **69%** (questions), **80%** (30 duplicate tickets) | same |
| Query rewriting at ~1,900 assets | top-5 recall 69% → **82%** | same |
| Fit judge, 30 duplicate tickets + 26 "nothing fits" controls, 109 assets | every findable duplicate caught; **0/26** false alarms | `experiments/scale/RESULTS_judge.md` |
| Same at ~9,600 assets | every findable duplicate caught; 3/26 false alarms | same |
| Search latency, 300k vectors, under row-level security | **~0.1 s** (index), 1.5 s (exact scan) | `experiments/scale/search_load_100k.txt` |
| Freshness, one hour of all public GitHub pushes replayed (111k pushes to main) | change to processed: p95 **225 s**, max 509 s | `experiments/sync/RESULTS.md` |
| Docs pages worth keeping (43 hand-labelled pages) | LLM: precision 0.60, recall 0.92; keyword rule: 0.26 / 0.46 | `experiments/pages/RESULTS.md` |

## Status

What holds up:
- Permissions. Tests check every user against every asset, and the database enforces
  the rules even when keyword search uses its index.
- Intake and freshness at large-org traffic, search speed with the vector index, and
  the judge's "use as-is" verdicts.

What is weak or unproven:
- **Recall drops as the org grows**, from about 94% to 69–80% top-5 at ~9,600 assets.
  Look-alike components from other teams are the main cause.
- **"Extend" verdicts get noisier at scale.** The answer currently says "Yes" for them.
- **LLM cost.** Each question makes two model calls, and nothing is cached across
  questions yet.
- **Evidence quality varies by language.** Test names are the main evidence. JavaScript
  `it("rejects a payload over 1 MB")` names say a lot; Python `test_max_age` says less.
- **Not production-hardened.** There is no SSO (users come from `add-user` and a header),
  no syncing of real repo permissions, no observability, and no real users yet.

See [VISION.md](VISION.md) for where it is going and [CHANGELOG.md](CHANGELOG.md) for
what changed.

## Development

```bash
docker compose up -d
pytest -q                 # needs the database; tests never call a live model
ruff check src tests
```

The test suite includes a permission leakage check that runs every user against every
asset. `scripts/` also holds the benchmark and evaluation scripts behind the results
above. They expect public repos cloned outside this directory and should be run with
`python -I`.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Please report security
issues privately, as described in [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE)
