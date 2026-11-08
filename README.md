<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.svg">
  <img alt="HomeGrown: has anyone here already built it?" src="docs/assets/hero-light.svg">
</picture>

<p align="center">
  <a href="https://github.com/tancred27/home-grown/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/tancred27/home-grown/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-4d4841"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-4d4841">
  <img alt="Status: prototype" src="https://img.shields.io/badge/status-prototype-f0652b">
</p>

<p align="center">
  <a href="https://tancred27.github.io/home-grown/?theme=dark"><b>Website</b></a> ·
  <a href="https://tancred27.github.io/home-grown/demo/?theme=dark"><b>Demo dashboard</b></a> ·
  <a href="#try-it"><b>Try it</b></a> ·
  <a href="docs/architecture.md"><b>How it works</b></a> ·
  <a href="docs/results.md"><b>Results</b></a>
</p>

Big companies build the same thing over and over, because most engineers, and their
coding agents, can only see their own team's code. Ask HomeGrown before you build. It
tells you what already exists, who owns it, how ready it is and how to get it, even when
the code is in a repository you can't open.

<img alt="Example answer: yes, xk6-kafka already does this. Beta, team-xk6-kafka in Developer Tools &amp; Testing, discoverable only: request access from the team lead." src="docs/assets/answer.svg">

<sub>A real answer from a test run on Grafana's public repositories. Public code has no org chart, so the teams, leads and access rules in that test are generated.</sub>

Two agents share one PostgreSQL database. A collector keeps a record of every component
as repositories change; an answerer searches it as the person asking, so row-level
security decides what each person sees. The [website](https://tancred27.github.io/home-grown/?theme=dark)
has the full tour.

## Try it

You need Docker, Python 3.12+ and Git, and a read-only GitHub token.

```bash
git clone https://github.com/tancred27/home-grown.git && cd home-grown
cp .env.example .env
docker compose up -d                                  # PostgreSQL with pgvector
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# collect one organization (pallets is small and public)
echo "HOMEGROWN_GITHUB_TOKEN_FILE=/path/to/github-token.txt" >> .env
python -I scripts/sync_worker.py pallets --once

# ask
homegrown add-user alice && export HOMEGROWN_USER=alice
homegrown ask "has anyone built signed tokens that expire, for password reset links?"
```

Without a model, HomeGrown answers from the evidence alone and says "possibly" rather
than "yes".

<details>
<summary><b>Add a model</b> (Anthropic, Groq, Gemini or a local one)</summary>

Set `HOMEGROWN_VERDICT_MODE=model` in `.env`, plus one of:

```bash
# pick one
HOMEGROWN_LLM_PROVIDER=anthropic   # with ANTHROPIC_API_KEY
HOMEGROWN_LLM_PROVIDER=groq        # with GROQ_API_KEY or HOMEGROWN_GROQ_KEY_FILE
HOMEGROWN_LLM_PROVIDER=gemini      # with GEMINI_API_KEY or HOMEGROWN_GEMINI_KEY_FILE
HOMEGROWN_LLM_PROVIDER=local       # with HOMEGROWN_LLM_BASE_URL and HOMEGROWN_LLM_MODEL (e.g. Ollama)
```

With a local model, nothing leaves your machines. Free hosted tiers may train on your
prompts, so don't send private code to them. More in [docs/configuration.md](docs/configuration.md).
</details>

**Dashboard:** run `uvicorn homegrown.api:app --port 8080` and open
<http://localhost:8080/ui>. There's no real sign-in yet: you type a user name, which is
fine on your own machine and not for a shared server.

## Use it from your coding agent

HomeGrown is an [MCP](https://modelcontextprotocol.io) server:

```bash
claude mcp add homegrown -e HOMEGROWN_USER=alice -- /path/to/.venv/bin/python -m homegrown.mcp_server
```

Agents call `find_existing` before writing new code. Each match says whether the person
asking can read its code; when they can't, the agent names the owner and offers to ask
for access instead of writing a copy.

## How well it works

Tested on real public code from Grafana, Microsoft, Google, AWS and others:

- **94%** of the time the right component is in the top 5 (109 components); **69–80%**
  at about 9,600.
- **26 of 26** times it says nothing fits when nothing does.
- **~0.1 s** search across 300,000 vectors, with permission checks on.

No company runs it in production yet, and accuracy drops as an org grows. Every number,
script and known limit is in [docs/results.md](docs/results.md); what's next is in
[VISION.md](VISION.md).

## Docs

[How it works](docs/architecture.md) · [Settings](docs/configuration.md) ·
[Connecting GitHub](docs/github.md) · [Running it for a company](docs/production.md) ·
[Traces and metrics](docs/observability.md) · [Results and limits](docs/results.md)

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), and report
security problems privately as described in [SECURITY.md](SECURITY.md).

Licensed under [Apache-2.0](LICENSE).
