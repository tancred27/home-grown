# Contributing to HomeGrown

Thanks for your interest. Bug reports, fixes, new source adapters and evaluation results
are all welcome.

## Before you start

- For anything bigger than a small fix, please open an issue first, so we can agree on the
  approach before you spend time on it.
- Read [docs/architecture.md](docs/architecture.md). It explains how the two agents and
  the database fit together.
- By contributing, you agree that your work is licensed under [Apache-2.0](LICENSE).

## Setting up

```bash
docker compose up -d                 # PostgreSQL 16 + pgvector, schema applied on first start
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
ruff check src tests
```

Tests need the database running. They never call a live model: anything that would call
one is stubbed. Please keep it that way, so the suite stays free and deterministic.

## Ground rules

These protect the people whose code and data HomeGrown indexes. Pull requests that break
them won't be merged.

- **Permissions live in the database.** Every read on the answer path runs as the asking
  user under row-level security (`db.as_user`). Don't add a query that uses the admin
  connection to answer a question. If you touch search or permissions, extend
  `tests/test_leakage.py`.
- **The application role is never a superuser.** Superusers bypass row-level security.
- **Every claim carries evidence.** A claim without an evidence reference is dropped.
- **The agent never deletes and never deprecates on its own.** At most, it records a
  proposal for a person to confirm.
- **Source text is data, not instructions.** READMEs, pages, comments and commit messages
  are untrusted. Models that read them get no tools, and their output must match a schema.
- **Fail safe.** If a model call fails, the result is `unknown`, never a guess.
- **Use public data only** in tests, fixtures and examples. No employer code or names, and
  no credentials.

## Code style

- Python 3.12, `ruff` clean (line length 100).
- Keep it simple and readable. Comments explain *why*, not *what*. Measurements and
  experiment notes belong in `experiments/`, not in docstrings.
- Prefer small functions with clear names over clever ones.

## Changing search or the judge

Retrieval and verdict changes need numbers. The evaluation scripts in `scripts/`
(`gate1_*`, `judge_eval.py`, `scale_*`) and their write-ups in `experiments/` show how
results were measured: choices are made on a tuning set and confirmed once on a held-out
set. Include before/after numbers in your pull request.

## Pull requests

- One topic per pull request, with tests.
- Describe what changed and how you checked it.
- Keep commits focused. Rebase on `main` before asking for review.

## Reporting bugs

Open an issue with what you ran, what you expected and what happened. For security
problems, follow [SECURITY.md](SECURITY.md) instead, and don't open a public issue.

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
