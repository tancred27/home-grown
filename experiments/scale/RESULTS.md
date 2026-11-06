# Scale with purpose lines for everyone: results

Run 2026-11-07, exactly as pre-registered in `PREREG.md` (committed `e10ec8d` before any
distractor line existed). Script: `scripts/scale_fair.py`. Raw output: `run_output.txt`,
per-question ranks: `results.json`. The purpose lines, written blind by two fresh Claude
instances from name + description only, are committed in `purpose_lines/` (1,909 lines,
0 missing; mean 9 words; 515 self-rated low confidence). Every condition ran inside one
rolled-back transaction; the database was verified unchanged afterwards (109 assets).

| | corpus | real units' purpose lines | distractors' | gate-1 test r@1 | **r@5** | r@10 | gate-2 dups top 5 | top 15 | top 30 |
|---|---|---|---|---|---|---|---|---|---|
| A0 | 109 | Groq, from README (production) | — | 0.74 | **0.96** | 0.98 | 29/30 | 29 | 30 |
| A | 1,932 | Groq, from README | none | 0.55 | **0.78** | 0.88 | 27/30 | 27 | 28 |
| C0 | 109 | Claude, name + description | — | 0.59 | **0.82** | 0.92 | 24/30 | 29 | 29 |
| **C** | **1,932** | **Claude, name + description** | **Claude, name + description** | **0.43** | **0.63** | **0.72** | **20/30** | **21** | **22** |

(gate-1 test: 51 queries whose gold exists, without keywords. gate-2: the 30 duplicate tickets.)

## What it says

**1. On equal terms, scale costs about 20 points of recall@5.** C0 → C: 0.82 → 0.63 on
the gate-1 test set, 24 → 20 of 30 gate-2 tickets in the top 5. The earlier "27/30 at
1,932" (condition A) was flattered by exactly the imbalance this run removed. Giving
everyone a purpose line does help compared with nobody having one (run 4c: 0.55), but
only modestly (0.63).

**2. What the purpose line is written from matters more than whether it exists.** At 109
assets, README-based lines (A0) beat description-only lines (C0) by 14 points (0.96 vs
0.82) and 5 tickets. Description-only lines average 9 words and are often generic (the
real `dskit` became "Toolkit of reusable components for building distributed systems").
A real deployment has every repo's README, so production lines would be the stronger
kind for *everyone*. The realistic number at this scale is therefore probably above 0.63,
but this run cannot say how far, because the distractors had no README input.

**3. A deeper shortlist doesn't rescue scale.** In C, the gold sits in the top 30 for only
22 of 30 tickets, and top 15 adds one ticket over top 5. The misses are spread
throughout, not just below the cut-off. (At 109 assets with weak lines, C0, top 15 does
recover 5 tickets. Weak lines push golds to ranks 6–15, and scale then pushes them
further.)

## Limits

- Distractor lines come from GitHub descriptions only (max 400 characters). The real
  units were reduced to their stored summary to match, so both sides are equally thin.
  That makes the comparison fair, but it is not what production would see.
- One writer family (Claude) for both sides; queries were also model-written.
- The gate-1 test set had been used before. It was only measured here, never tuned on.

## What would settle the realistic number

The same run with README-based lines for all 1,909 items: fetch each distractor's README
via the GitHub API (~1,823 calls, under an hour of rate limit) and have blind writers
produce lines from name + description + README excerpt, the same input
`collector/purpose.py` uses in production. If that closes most of the gap, scale is a
data-quality problem the product already solves. If not, retrieval itself needs work:
rewording the ticket in the org's vocabulary, filters by language or LOB, or a
cross-encoder trained for this.

---

# Round 2 (2026-11-07): making it scale

## Code-derived purpose lines (condition K, pre-registered addendum)

READMEs are often outdated, so K writes every purpose line from code-derived signals only
(`collector/signals.py`): name, one-line description, language, manifest module and
imports, directory names, source and test file names. Same blind setup (four fresh
Claude instances, opaque ids). Lines in `code_lines/`.

| gate-1 test (51) | 109 assets r@5 | ~1,932 assets r@5 | gate-2 dups top 5 at scale |
|---|---|---|---|
| A0/A: README-based (Groq), distractors without lines | 0.96 | 0.80 (flattered) | 27/30 |
| C: description only, everyone | 0.82 | 0.63 | 20/30 |
| **K: code signals, everyone** | **0.90** | **0.69** | 19/30 |

**Decision rule (fixed in advance): not met.** K0 is 0.06 below A0 (the threshold was
0.05), so production does not switch to code-only purpose lines. Two confounds keep this
from being a verdict on code signals: the README lines came from a different writer
(Groq gpt-oss-120b), and no "code signals + README" condition was run. K does beat
description-only at scale (0.69 vs 0.63), and its lines are visibly richer (dskit:
"…hash rings, KV stores, memberlist, caching, gRPC server middleware, tracing and
multi-tenancy").

## Query rewriting: the biggest gain at scale

The model restates the request as the purpose line of a component that would satisfy it.
That text joins the search (`rw+`). Variants were compared on the **tuning set only**
(106 questions), with K lines, inside rolled-back transactions
(`scripts/scale_variants.py`). Rewrites come from a blind Claude instance that saw only
the requests (`rewrites_claude.json`), because the Groq daily cap was spent.

| tuning set | 109 r@1 | 109 r@5 | ~1,932 r@1 | ~1,932 r@5 | ~1,932 r@10 |
|---|---|---|---|---|---|
| base (production search) | 0.64 | 0.86 | 0.32 | 0.60 | 0.66 |
| semantic only | 0.64 | 0.86 | 0.32 | 0.58 | 0.72 |
| purpose field only | 0.61 | 0.82 | 0.34 | 0.54 | 0.67 |
| **rw+ (rewrite appended)** | **0.82** | **0.97** | **0.58** | **0.80** | **0.87** |
| rewrite only, semantic | 0.76 | 0.94 | 0.54 | 0.78 | 0.86 |
| best of query/rewrite, semantic | 0.77 | 0.93 | 0.55 | 0.76 | 0.84 |

Chosen: rw+. **Confirmed once on the locked test set** (51): r@5 0.90 → **0.98** at 109
and 0.69 → **0.82** at ~1,932; r@1 0.49 → 0.57. **On the 30 duplicate tickets at
~1,932 assets: 19 → 28 of 30 in the top 5.** Now in production (`settings.query_rewrite`,
one small LLM call per question, fails safe to plain search).

## Vector search at 1 million (`ann_1m.txt`, `scripts/bench_ann_rls.py`)

1M clustered synthetic 768-d vectors (halfvec), a real RLS policy, queries as the app
role. Recall is measured against the exact answer for the same user.

| | 95% org-wide | 50% org-wide |
|---|---|---|
| exact scan p50 / p95 | 208 / 220 ms | 717 / 811 ms |
| HNSW ef=200 p50 / p95 | 4 / 10 ms | 4 / 9 ms |
| HNSW ef=200 recall@10 | 0.83 | 0.88 |
| queries returning short | 0 | 0 |

Exact scan stays usable to ~1M vectors (≈330k assets with 3 fields). Beyond that,
`settings.vector_search = "hnsw"` fetches a 400-row candidate pool through the index
(iterative scan) and ranks the pool exactly. The silent "zero results under RLS"
failure from early in the project didn't occur: in this product most cards are
org-wide, so the filter is rarely selective. Not yet measured: recall of the
pool-then-rank mode at 1M (it needs another ~25-minute index build).

## Write path

- **No clones needed:** `collector/remote.sparse_checkout` fetches the tree + manifests +
  READMEs + touched tests through the API (1 + ~5–30 calls per new commit, cached per
  tree SHA).
- **Incremental dependency refresh:** `usage.refresh(repos=…)` reads only the repos a
  batch touched; published names are stored in `package_name`.
- **No human bottleneck:** owner approval is off by default.

## What still limits "millions"

- **Search quality** is still the binding constraint, not speed: 0.82 r@5 at ~1,900
  assets with rewriting. Untested next steps: code signals + README together, filtering
  by language or LOB, and a rerank by the fit judge over a deeper pool.
- **One-off backfill cost:** ~400 tokens per unit for its purpose line (≈400M tokens per
  million units), and ~3 embeddings per unit on CPU (≈50–90 texts/s on this laptop; a
  GPU or a hosted embedder is needed for millions).
- The 1M search benchmark is synthetic. Real embeddings at 1M weren't affordable on this
  laptop.
