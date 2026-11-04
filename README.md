# Org Capability Memory (starter)

An agent-maintained memory of what an organization has already built, so engineers and
coding agents check before rebuilding.

**The problem:** in most large organizations repositories are scoped to teams, so a
developer — and their coding agent — cannot see what other teams have built. That is a
permission boundary, not a search problem, and it is tightening. A permission-aware
memory is the only thing positioned to answer across it.

Contents
- `docs/SCOPE.md` – **the current plan.** Problem, storage model, expansion path, the
  POC, and how we know it works. Supersedes the milestone plan in `docs/design.md`.
- `CLAUDE.md` – standing rules for coding sessions in this repo.
- `docs/INVENTORY.md` – what carries forward from the starter bundle,
  which findings are trustworthy, which must be redone, and what is still missing.
- `docs/HANDOFF.md` – everything decided and learned in the design session, including
  rejected ideas and sandbox anomalies.
- `docs/design.md` – the full design document (HLD + LLD). Sections 5 (data model),
  6 (ingestion agent) and 8 (visibility) stand; sections 9–10 are superseded by
  `docs/SCOPE.md`.
- `experiments/` – scripts, outputs and `RESULTS.md` from the feasibility runs. Read
  `docs/INVENTORY.md` before trusting any number in `RESULTS.md`.

## Re-running the experiments
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r experiments/requirements.txt
# A. unit detection
experiments/clone_repos.sh /path/to/fresh/empty/repos
mkdir -p out
for r in /path/to/fresh/empty/repos/*; do python -I experiments/units.py "$r" out/$(basename "$r").json; done
python -I experiments/gt.py out /path/to/fresh/empty/repos
# B. permissions in Postgres (see experiments/setup_postgres.md first)
python -I experiments/expb.py && python -I experiments/expb2.py
# D. retrieval with different wording
experiments/fetch_npm.sh /path/to/fresh/npm
python -I experiments/expd.py /path/to/fresh/npm
python -I experiments/expd2.py experiments /path/to/fresh/npm   # with agent-style keyword expansion
```
Note: `expd2.py` reads `expd.py` from the directory in its first argument. Paths above are examples.

Experiments A and B are reusable. Experiment D is kept only as a record: its relevance
labels are derived from keyword-ranked npm search, so its headline numbers are not
trustworthy — see `docs/INVENTORY.md`.

First real task: rebuild D properly for gate 1 — a real embedding model, and 50
paraphrase queries with human-judged relevance over real tool descriptions.
