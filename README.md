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

### Asking the memory

The same `find_existing` is reachable three ways; every call is logged to `question`
with its route, so which route agents actually use is measurable.

```bash
orgmem find "turn request paths with ids into route templates" -k cardinality,metrics

python -m orgmem.mcp_server                     # MCP over stdio
claude mcp add orgmem -e ORGMEM_USER=gate2-dev -- .venv/Scripts/python -m orgmem.mcp_server
```

`ORGMEM_VERDICT_MODE=model` swaps the rule-based verdict for a model judge (needs API
credit; fails safe to `unknown` without it). See `experiments/gate2/RESULTS.md` for why
it matters: the rule-based verdict raised a false alarm on every control ticket.

### Gate 2: the coding-agent harness

```bash
python -I scripts/gate2_run.py --model scripted-eager      # plumbing, no LLM
python -I scripts/gate2_run.py --model claude --repeats 3  # the measurement (API credit)
```

A LangGraph agent (`src/orgmem/agent/`) with a Postgres checkpointer and an
`interrupt()` that asks reuse-or-build. Five nudge conditions, from a plain tool
description to a hook where the graph itself checks before the first write.

### GPU (optional)

Embedding and reranking run on onnxruntime. The CPU build lives in `.venv`; the CUDA
build needs its own environment, because `onnxruntime` and `onnxruntime-gpu` install the
same module and cannot coexist:

```bash
py -3.13 -m venv .venv-gpu
.venv-gpu/Scripts/python -m pip install "onnxruntime-gpu[cuda,cudnn]==1.31.1" "fastembed-gpu==0.9.1"
.venv-gpu/Scripts/python -m pip install -e . --no-deps      # don't pull CPU onnxruntime back in
.venv-gpu/Scripts/python -m pip install fastapi "uvicorn[standard]" "psycopg[binary]" pgvector \
    pydantic pydantic-settings httpx langgraph langgraph-checkpoint-postgres numpy pytest pytest-asyncio
.venv-gpu/Scripts/python -I scripts/check_gpu.py            # confirms the ACTIVE provider
```

The code is the same in both: `ORGMEM_DEVICE=auto` (default) uses CUDA when it is
available and CPU otherwise. Always run `check_gpu.py` after setup — onnxruntime falls
back to CPU silently when a CUDA or cuDNN DLL is missing, so "it ran" is not proof.
