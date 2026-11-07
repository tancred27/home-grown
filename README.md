<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.svg">
  <img alt="HomeGrown: has someone in my company already built this?" src="docs/assets/hero-light.svg">
</picture>

<p align="center">
  <a href="https://github.com/tancred27/home-grown/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/tancred27/home-grown/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-2f7d46"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-2f7d46">
  <img alt="Status: prototype" src="https://img.shields.io/badge/status-prototype-c98a1b">
</p>

## Why

Big companies build the same thing over and over. One team writes a retry library, and
three teams later someone writes another. It's usually not laziness: most engineers can
only see their own team's code, and so can their AI coding assistant.

HomeGrown fixes that. Ask it in plain English, and it tells you:

- **what already exists** that does what you need,
- **who built it**, their team and their manager,
- **whether it's ready**: still in development or already in production,
- **how to get it**: read the code yourself, or who to ask for access.

It can do this even when you're not allowed to see the code. Knowing something exists
and who to ask is often enough.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/how-it-works-dark.svg">
  <img alt="1. It watches your code and docs. 2. It remembers what each piece does, who built it, and whether it is live. 3. It answers questions, showing only what you may see." src="docs/assets/how-it-works-light.svg">
</picture>

Two AI agents share one database. The **collector** keeps track of everything your
company has built. The **answerer** finds the best matches for a question and checks
each one actually fits. It would rather say "I don't know" than send you to the wrong
place.

## What an answer looks like

<img alt="Example answer: MarkupSafe and Jinja2 both escape HTML, both in production, with team, manager and how to get access" src="docs/assets/answer.svg">

<sub>From a real run on the public <a href="https://github.com/pallets">pallets</a> organization, trimmed for space. People's names are replaced with pseudonyms.</sub>

## Try it

You need Docker, Python 3.12+ and Git. It takes about five minutes.

**1. Install**

```bash
git clone https://github.com/tancred27/home-grown.git && cd home-grown
cp .env.example .env
docker compose up -d
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

**2. Choose an AI model** (optional, but answers are much better with one)

Add these to `.env`. Groq, Anthropic, Gemini and local models such as Ollama all work.

```bash
HOMEGROWN_LLM_PROVIDER=groq
HOMEGROWN_VERDICT_MODE=model
HOMEGROWN_GROQ_KEY_FILE=/path/to/groq-key.txt
```

**3. Point it at a GitHub organization**

```bash
echo "HOMEGROWN_GITHUB_TOKEN_FILE=/path/to/github-token.txt" >> .env   # read-only token
python -I scripts/sync_worker.py pallets --once
```

**4. Ask**

```bash
homegrown add-user alice && export HOMEGROWN_USER=alice
homegrown ask "has anyone built a way to escape HTML safely?"
```

## Use it from your AI coding assistant

HomeGrown speaks [MCP](https://modelcontextprotocol.io), so assistants like Claude Code
and Cursor can check it before they write new code:

```bash
claude mcp add homegrown -e HOMEGROWN_USER=alice -- /path/to/.venv/bin/python -m homegrown.mcp_server
```

## How well it works

Measured on real public code from Grafana, Microsoft, Google, AWS and others:

| | |
|---|---|
| Finds the right component in its top 5 | **94%** in a small org, **69–80%** at ~9,600 components |
| Says "nothing fits" correctly | **26 of 26** times in a small org, 23 of 26 at ~9,600 |
| Search speed | **~0.1 s**, even with 100,000 components |
| Picks up new code | within **4 minutes** for 95% of changes, at the scale of all of GitHub |

More detail and every limit we know of: [docs/results.md](docs/results.md).

## Status

HomeGrown is a **working prototype**. It has been tested on real code, but not yet inside
a real company. Sign-in, permission sync and monitoring are still to come. See
[VISION.md](VISION.md) for what's next.

## Learn more

- [How it works, in detail](docs/architecture.md)
- [Settings](docs/configuration.md)
- [Connecting GitHub](docs/github.md)
- [Running it for a whole company](docs/production.md)
- [Traces and metrics](docs/observability.md)
- [Results and limits](docs/results.md)

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md). Please report
security problems privately, as described in [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE)
