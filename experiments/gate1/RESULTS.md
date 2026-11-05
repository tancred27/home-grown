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
