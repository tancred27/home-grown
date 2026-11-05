# Gate 1: retrieval when the wording differs

Run 2026-11-05. Pass line: hybrid recall@5 ≥ 0.80 (`docs/design.md` section 9).
Reproduce: `python -I scripts/gate1_retrieval.py`

Corpus: 86 assets from 34 Grafana repos (1,054 layer-2 claims, 83 layer-3).
Queries: 51 hand-written, one gold asset each, in `queries.json`.

## Method, and what was fixed from experiment D

Experiment D's headline was circular: labels came from keyword-ranked npm search, so
"relevant" silently meant "lexically matches the topic phrase". Two changes:

1. One **gold asset per query**, chosen by reading the asset — not derived from any
   ranking function.
2. **Lexical overlap is measured**, and recall is reported stratified by it. The queries
   are still author-written, so vocabulary leaks in; the difference is that the leak is
   now quantified rather than assumed. The low-overlap stratum is the real result.

Searching runs as an auditor who can see every asset, so this isolates retrieval from
permissions (`tests/test_leakage.py` covers those).

## Result

| method | recall@1 | recall@5 | recall@10 | MRR | misses |
|---|---|---|---|---|---|
| keyword | 0.43 | 0.67 | 0.73 | 0.53 | 10 |
| semantic | **0.55** | 0.76 | **0.86** | **0.64** | **2** |
| hybrid (max-fusion) | 0.47 | **0.80** | 0.86 | 0.63 | 4 |

| measured overlap | n | keyword@5 | semantic@5 | hybrid@5 |
|---|---|---|---|---|
| low < 0.20 | 28 | 0.54 | **0.71** | 0.68 |
| mid 0.20–0.45 | 19 | 0.79 | 0.79 | **0.95** |
| high ≥ 0.45 | 4 | 1.00 | 1.00 | 1.00 |

## Verdict: the gate reads PASS at exactly 0.80. Treat that as a FAIL.

Three reasons the headline number does not support an 80% claim:

1. **The fusion method was chosen after seeing the failure**, on this same query set.
   The first run used textbook reciprocal-rank fusion and scored 0.76, *worse than
   semantic alone*. Switching to max-fusion lifted it to 0.80. That is fitting the
   method to the evaluation set — the exact error experiment D made in a different
   costume. A held-out query set is required before quoting 0.80.
2. **The honest stratum is 0.68.** On the 28 low-overlap queries — where the asker does
   not share the asset's vocabulary, which is the case the whole design exists for —
   hybrid gets 0.68 and plain semantic gets 0.71. Both are below the line.
3. **86 assets is a small corpus.** Recall@5 over 86 candidates is far easier than over
   the thousands a real org would have. This number will fall with scale.

## Two real bugs this gate found

**`plainto_tsquery` ANDs every term.** The first run showed keyword recall of 0.00 with
51 misses — not a finding, a broken lexical path. A sentence-length intent like "join
several running processes into a ring" required all eight words to be present and
matched nothing, so the lexical half of the product's hybrid search contributed exactly
zero while the vector half silently carried the result. Fixed by building an OR'd
`to_tsquery` (`search.py::_tsquery_or`). This is the class of bug that looks like a
working system until something measures it.

**Summing reciprocal ranks punishes the better retriever.** With K=60 and 20 candidates
all reciprocal ranks sit between 1/61 and 1/81, so "appears in both lists" nearly always
outscores "ranked first by the better list". One gold asset ranked **#1** by vector
search was pushed to **#11** by fusion. Max-fusion takes the best evidence rather than
rewarding agreement, with a tie-break on having both signals to keep some
complementarity.

## Diagnosis: several misses are ingestion failures, not retrieval failures

The queries missed by *every* method share a cause — the layer-1 summary is useless,
because the README heuristic takes the first non-heading line and that is often a title
or a notice:

| asset | extracted summary |
|---|---|
| `carbon-relay-ng` | `"carbon-relay-ng"` — the title |
| `dataplane:sdata` | `"> This sdata package is still in experimental stage..."` |
| `xk6-kafka` | `"> [!NOTE]"` |

No retrieval method can find a thing described by its own name. **Fixing the summary
extractor is likely worth more than any retrieval tuning** — it is the cheapest
available gain and it was invisible until the gate ran.

## Run 2: summary fix, reranker, and a held-out set

The held-out set (`queries_heldout.json`, 58 fresh phrasings, 55 resolvable) was
**committed before either fix was written** — see commit `3f28728` — so neither fix
could have been shaped to it. The original 51 queries are now the dev set.

### Fix 1 — better layer-1 summaries: kept, it helped

The source system's own repo description is now preferred for the repo-level unit, and
the README fallback skips titles, badges, admonitions and status notices. All 86 assets
now have a real summary (`carbon-relay-ng` went from `"carbon-relay-ng"` to "Fast
carbon relay+aggregator with admin interfaces for making changes online"). On the dev
set, semantic recall@5 on low-overlap queries rose from 0.71 to 0.79.

### Fix 2 — a cross-encoder reranker: rejected, it hurt

| | dev hybrid@5 | held-out hybrid@5 | held-out low-overlap |
|---|---|---|---|
| with reranker (jina-tiny) | 0.61 | 0.62 | 0.46 |
| **without reranker** | **0.86** | **0.73** | **0.61** |

Two of the three small cross-encoders available locally were broken in this build:
both Xenova ms-marco exports (L-6 and L-12) returned scores that rose monotonically
with the document's **position**, regardless of content — -11.44, -11.40, -11.32 on a
case with one obviously right answer, which they ranked last. In the first rerank run
this pushed an asset ranked #1 by *both* retrievers down to #15. The working model
(jina-reranker-v1-tiny) ranked trivial cases correctly but with a weak margin, and on
real queries still lost 11 points of recall@5. The reranker is kept in the code, off by
default, as a negative result. A larger reranker might behave differently; that is
untested.

### The result

| held-out set (n=55) | recall@1 | recall@5 | recall@10 | MRR | misses |
|---|---|---|---|---|---|
| keyword | 0.24 | 0.56 | 0.76 | 0.37 | 12 |
| semantic | **0.49** | 0.71 | **0.85** | **0.60** | 4 |
| hybrid (max-fusion) | 0.24 | **0.73** | 0.84 | 0.46 | **3** |

| held-out, by measured overlap | n | keyword@5 | semantic@5 | hybrid@5 |
|---|---|---|---|---|
| low < 0.20 | 28 | 0.36 | 0.57 | **0.61** |
| mid 0.20–0.45 | 21 | 0.76 | 0.86 | 0.86 |
| high ≥ 0.45 | 6 | 0.83 | 0.83 | 0.83 |

**Gate 1: FAIL. Held-out hybrid recall@5 = 0.73 against a line of 0.80. The low-overlap
stratum is 0.61.**

### The most useful number here is the gap

The same system scores **0.86 on the dev set and 0.73 on the held-out set** — a
13-point gap, and 0.82 vs 0.61 on the low-overlap stratum. Nothing changed between those
two numbers except whether the queries were seen while choosing the method. That gap is
the cost of tuning on your evaluation set, measured directly, and it is the error that
invalidated experiment D. Without the held-out set this project would currently be
reporting a pass.

### An unresolved trade-off

Max-fusion buys a little recall@5 (0.73 vs 0.71 — one query out of 55) at a large cost
in recall@1 (0.24 vs 0.49) and MRR (0.46 vs 0.60). For the verdict step, which reads the
top few candidates, recall@5 is what matters; for a person scanning results, the top
result matters. **Not decided, and deliberately so**: choosing between them by looking at
these numbers would spend the held-out set as a tuning set. Any further method choice
needs a third query set.

## Run 3: closing the gap, with a third query set

Run 2 looked at the held-out set, so it can no longer choose anything. Dev + held-out
(106 queries) became the **tuning set**, and a third set — `queries_test.json`, 52
queries, each carrying the `keywords` an agent would pass — was committed in `76e6b4e`
**before any run-3 method was tried**. Pre-registered in that commit: the test set runs
once, with the method the tuning set selects; the keywords effect is reported as a
with/without comparison whichever way it goes.

### Variants, on the tuning set (n=106, 56 low-overlap)

| variant | r@1 | r@5 | r@10 | MRR | low r@5 |
|---|---|---|---|---|---|
| A vec, `card` field (run-2 semantic) | 0.53 | 0.74 | 0.85 | 0.63 | 0.68 |
| B vec, `summary` field only | 0.56 | 0.75 | 0.82 | 0.65 | 0.68 |
| C vec, max over both fields | **0.58** | 0.78 | 0.86 | 0.67 | **0.71** |
| D max-fusion lexical + A (run-2 hybrid) | 0.33 | 0.79 | 0.88 | 0.54 | 0.71 |
| E max-fusion lexical + C | 0.34 | **0.81** | 0.89 | 0.56 | 0.71 |
| F weighted RRF (w=0.75) | 0.48 | 0.74 | **0.90** | 0.61 | 0.55 |
| **H interleave C then lexical** | **0.58** | 0.80 | 0.89 | **0.68** | 0.70 |
| G = H + bge-reranker-base | 0.40 | 0.61 | 0.77 | 0.50 | 0.43 |

**The clean `summary` field helped a little.** The `card` vector embeds name + summary +
up to 20 claim bodies, mostly test names — the same dilution that broke the reranker's
input. Taking the best of the two fields per asset lifted semantic recall@5 0.74 → 0.78.

**Max-fusion's recall@1 collapse has a mechanical cause.** Semantic #1 and lexical #1
both score exactly 1/61, so which comes first is arbitrary, and lexical #1 is often
noise. Breaking the tie toward semantic — the measured-stronger retriever — is the same
as interleaving the two lists (H). It keeps max-fusion's recall@5 and restores
semantic's recall@1: the best MRR of any variant.

**A third reranker made things worse.** bge-reranker-base (1GB) cut recall@5 from 0.80
to 0.61. With jina-tiny and two broken ms-marco exports before it, that is every
cross-encoder tried. They are trained on web passages and question answering; these
documents are one-line capability descriptions plus test names, and the queries are
requirements. That domain mismatch is the likely cause, and it would not be fixed by
running them faster on a GPU.

## What is needed to actually pass

1. A **held-out** query set, written before the method is tuned.
2. A better layer-1 summary extractor (above).
3. A **reranker** — the design calls for one in section 7 and it is not built. Scoring
   the top ~50 with a cross-encoder is the standard fix for exactly this gap.
4. A larger corpus, to see how the number moves with scale.
5. `keywords`-augmented queries, measured separately. The design exposes a `keywords`
   input on `find_existing` for the caller's synonyms; its value is untested here.

## Caveats

- 51 queries, one author, no second rater. The overlap bands have small n (4 in the
  high band).
- Author-written queries carry unavoidable vocabulary bias even when deliberately
  avoided; the overlap measurement bounds it but does not remove it.
- The `augurs` repo contributes ~20 near-identical sub-crates, which makes several gold
  labels genuinely confusable. That is realistic for a monorepo, and it is part of why
  recall@1 is low, but it is worth separating in a future run.
