# Gate 2: do coding agents check the memory before building?

Pass line (`docs/design.md` section 9): coding agents call `find_existing` in at least 60%
of runs where a duplicate exists, under the strongest nudges.

**Status: the harness is built and tested; the gate itself is not yet measured.** The
measurement needs a real model driving the agent (`scripts/gate2_run.py --model claude`),
and the project's Anthropic API key had no credit (`400: credit balance is too low`).
Everything below is what can be established without it — which turned out to include
the most important finding so far about the product's verdict.

## The harness

`src/orgmem/agent/` is a LangGraph coding agent (see `graph.py`):

```
START -> agent -> (no tool calls) ------------------------------------------> END
           |
           +--> hook_check --(strong match)--> decide --> tools --> agent
           |        +--(no match)------------> tools
           +--> tools --(model's own check found a strong match)--> decide --> agent
```

- Tools: a sandboxed workspace (read, write, list; paths cannot escape) and
  `find_existing` over the real corpus and retriever.
- Five nudge conditions (`tools.py`): `none`, `description` (the design's "call this
  BEFORE writing" wording), `instruction` (a system-prompt line, as an `AGENTS.md` would
  add), `all` (both), and `hook` — where the graph itself checks before the first write,
  so adherence no longer depends on the model choosing to.
- `decide` pauses with `interrupt()` when a check returns a use-as-is / extend
  candidate, asks reuse-or-build, and resumes with `Command(resume=...)`. With the
  Postgres checkpointer the pause survives a restart — tested by resuming in a new graph
  instance (`tests/test_gate2_agent.py::test_pause_survives_a_restart...`).
- `score_run` turns each run's event log into the gate-2 metrics: did the model check
  before its first write; did any check happen; was the duplicate found; was it reused.
- `scripts/gate2_run.py` runs variant × ticket × repeat with a simulated developer who
  answers "reuse" only when shown the right asset.

## What was measurable without an LLM

The `hook` condition checks using the ticket text and the real retriever, so with a
scripted stand-in model (`scripted.py`, plumbing only) its retrieval figures are real.

**Development tickets** (`tasks.json`: 12 duplicate, 4 control; 80 runs, 0 errors):
the hook's check found the duplicate in **11/12** tickets — and raised a **false alarm on
4/4** control tickets.

**Why: the rule-based verdict never looks at relevance.** Any candidate with test claims
is "use_as_is", and search always returns something, so every ticket gets a reuse
prompt. A developer asked "this already exists — reuse it?" on every ticket learns to
click "build anyway" without reading, which is the approval-fatigue failure the design
warns about.

### The obvious fix fails, and why that matters

The natural fix is a minimum similarity below which a candidate cannot be recommended.
It was calibrated on gate-1 tuning queries only (`scripts/calibrate_verdict.py`,
`calibration.txt`): similarity of each query to its gold asset, against leave-one-out
negatives — the best similarity to any *other* asset, i.e. what the top match looks
like when the wanted thing does not exist.

| | 5th | 25th | median | 75th | 95th percentile |
|---|---|---|---|---|---|
| query vs its gold asset | 0.539 | 0.576 | 0.627 | 0.690 | 0.786 |
| best match when gold is absent | 0.517 | 0.568 | 0.612 | 0.660 | 0.749 |

The distributions nearly coincide; the best threshold (0.68) keeps 28% of true matches
at a 12% false-alarm rate (Youden J = 0.16). Not adopted.

**Retrieval can rank, but it cannot detect absence.** When the right asset exists,
search puts it near the top; when it does not, the top result looks just as similar.
"Does this already exist?" is therefore not a retrieval question at all — it needs a
judgment that reads the request against the candidate. This is the design's verdict
step (section 7.5), and the measurement says it is not optional.

### A blind model judge, on held-out tickets

`tasks_heldout.json` — 6 duplicate and 8 control tickets — was committed (`3875455`)
before calibration and before this comparison was declared (`2691651`). For each ticket,
the top-5 candidates from the real retriever were written to a judge input file holding
the ticket and the candidate cards only; the letter → asset key was kept separately
(`judge_key_heldout.json`). A fresh Claude subagent that had seen neither the tickets'
labels nor the corpus judged every candidate (`judge_out_heldout.json`), with the
design's instruction to be conservative.

| held-out tickets | duplicates flagged | false alarms on controls |
|---|---|---|
| rule-based verdict | 4 / 6 | **8 / 8** |
| **model judge, blind** | **6 / 6** | **0 / 8** |

Of 60 not-a-fit verdicts, none was on a gold asset. The 4 strong verdicts on non-gold
assets are all siblings of the gold one — the JS bindings and parent toolkit of the
changepoint crate, the init helper and parent repo of the signal-safe memory reader —
which would send a developer to the right place, but are counted against the judge under
strict scoring.

Limits: 14 tickets is a small sample; the judge was a subagent, not the product's own
`llm.py` path, and it saw the LLM-written purpose lines, which the product's judge does
not yet store; and one model judged, with no second rater.

### In the product

`find_existing` now has `settings.verdict_mode`: `"rules"` (default for now) or
`"model"`, which calls `judge_with_model` — one call judging all top candidates, with
the conservative instruction above. **It fails safe**: on any error every candidate is
`unknown`, so a broken judge can never send a developer to the wrong asset. Verified
against the live API with the uncredited key: every verdict came back `unknown —
model verdict unavailable (BadRequestError)`, and the agent would interrupt no one.
`tests/test_model_verdict.py` covers the mapping and the fail-safe.

### A production-path judge: Groq `openai/gpt-oss-120b` (2026-11-06)

The subagent judge above was not the product's code path. This run is: the product's own
`search.judge_with_model` through `llm.py`, on Groq's free tier, model pinned for the
whole run, every call recorded (`judge_eval_groq.json`, cached in
`judge_cache_groq.json`). Script: `scripts/judge_eval.py`. Inputs per ticket: the real
retriever's top 5 as the asking developer may see them — name, description, LLM-written
purpose line, non-sensitive evidence. Never the gold label.

Two ticket sets: the 14 held-out tickets (the subagent judge's set, for comparison) and
12 fresh ones committed before any judge run on them (`tasks_judge_fresh.json`,
`81b256a`).

| | duplicates flagged | false alarms on controls | unrelated wrong-asset |
|---|---|---|---|
| rule-based verdict, held-out 14 | 4 / 6 | 8 / 8 | — |
| Claude subagent judge, held-out 14 | 6 / 6 | 0 / 8 | 0 (4 siblings) |
| **gpt-oss-120b, held-out 14** | **5 / 6** | **0 / 8** | **0** |
| **gpt-oss-120b, fresh 12** | **4 / 4** | **0 / 8** | **0** |

**9 of 10 duplicates flagged, 0 false alarms on 16 controls, 0 failed calls**, about 1 s
per judgment. The 5 strong verdicts on non-gold assets are all siblings in the gold's
repository (the JS binding of the same crate, the repo's own Helm chart) and would send a
developer to the right place.

The one miss is defensible and may be a labelling error of mine: ticket h02 asks for a
review-bot config generated *from CODEOWNERS*; the asset's description says it generates
policy-bot config "to help you enforce conditional workflows have passed", with nothing
about CODEOWNERS, and the judge said so. The ticket assumed a feature the evidence shown
does not support.

**Bugs this run depended on fixing first.** An earlier Gemini attempt found 0 of 6
duplicates. The cause was in the product: verdicts were mapped to candidates by the first
character of the label, so "Candidate A" became "C" (fixed in `566d0e0`; the label is now
schema-restricted to the letters shown). That run also exhausted Gemini's free tier:
**20 requests per day per model** — too small for this workload.

Limits: 26 tickets, one rater for the gold labels, one model; free-tier output is
acceptable here only because every input comes from public repositories.

## What gate 2 still needs

1. **Credit on the API key**, then `python -I scripts/gate2_run.py --model claude
   --repeats 3` with `ORGMEM_VERDICT_MODE=model`. That run answers the gate: the
   `model checked` column across `none` / `description` / `instruction` / `all`, against
   the `hook` row, is the comparison the design is built around.
2. More tickets, especially controls — a false-alarm rate needs more than 8 to be
   quoted with any confidence.
3. Other coding agents. The same `find_existing` is reachable over a CLI and MCP
   (`orgmem.cli`, `orgmem.mcp_server`), which is how agents other than this harness —
   Claude Code, Codex, Cursor — can be measured on the same tickets.
