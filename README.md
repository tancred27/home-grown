# Org Capability Memory (starter)

An agent-maintained memory of what an organization has already built, so engineers and
coding agents check before rebuilding.

**The problem:** in most large organizations repositories are scoped to teams, so a
developer — and their coding agent — cannot see what other teams have built. That is a
permission boundary, not a search problem, and it is tightening. A permission-aware
memory is the only thing positioned to answer across it.

Contents
- `docs/design.md` – **start here.** The design: problem, solution, HLD and LLD, with
  every number linked to the experiment that produced it.
- `docs/SCOPE.md` – what is in the first build and what is deliberately cut.
- `CLAUDE.md` – standing rules for coding sessions in this repo.
- `docs/INVENTORY.md` – what carries forward from the starter bundle,
  which findings are trustworthy, which must be redone, and what is still missing.
- `docs/HANDOFF.md` – everything decided and learned in the design session, including
  rejected ideas and sandbox anomalies.
- `docs/design-v1.md` – the original design from the first session, kept for history.
  Superseded by `docs/design.md`; read it only to see what changed and why.
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

## Running it

```bash
docker compose up -d                       # Postgres 16 + pgvector 0.8, schema auto-applied
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"

# Build a corpus. Both dirs are untrusted data; keep them outside the repo.
experiments/density/fetch.sh  <comments-dir>
python -I scripts/ingest.py   <repos-dir> <comments-dir>

python -I scripts/demo_boundary.py "reject a payload that is too large"
python -I scripts/bench_vector.py 50000 384 768 1024
.venv/Scripts/python -m pytest -q           # includes the permission leakage gate
```

One synthetic team per repo, each user in their own team only — so cross-team
discovery is blocked by row-level security rather than by convention.
