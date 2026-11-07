# Gate 1: retrieval when the wording differs

Run 2026-11-05. Pass line: hybrid recall@5 ≥ 0.80 (the design notes).
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

### Embedding models, on the tuning set

Semantic retrieval carries almost all of the low-overlap recall, so the largest
remaining lever looked like the embedding model. `scripts/gate1_models.py` compares
models in memory (numpy exact scan over 86 assets, identical to the database's exact
scan) so models of different dimension need no schema change, and gives each model its
own documented query/document prefixes. Retrieval as variant H: max over the `card` and
`summary` texts, interleaved with lexical. The bge-base row reproduces variants C and H
exactly, which confirms the in-memory path measures the same thing as the database.

| model | dim | r@1 | r@5 | r@10 | MRR | low r@5 | hybrid r@5 | CPU encode |
|---|---|---|---|---|---|---|---|---|
| **BAAI/bge-base-en-v1.5** (current, quantized) | 768 | **0.58** | **0.78** | 0.86 | **0.67** | **0.71** | **0.80** | 16 s |
| BAAI/bge-large-en-v1.5 | 1024 | 0.50 | 0.75 | 0.88 | 0.62 | 0.61 | 0.76 | 398 s |
| mixedbread-ai/mxbai-embed-large-v1 | 1024 | 0.51 | 0.73 | **0.92** | 0.62 | 0.61 | 0.75 | 662 s |
| nomic-ai/nomic-embed-text-v1.5 | 768 | 0.40 | 0.62 | 0.76 | 0.51 | 0.43 | 0.65 | 168 s |
| snowflake/snowflake-arctic-embed-l | 1024 | 0.49 | 0.70 | 0.81 | 0.59 | 0.59 | 0.74 | 628 s |
| thenlper/gte-large | 1024 | *not run — skipped to save a 1.2 GB download on a ~0.3 MB/s link* | | | | | | |

**Bigger was not better.** All three large models lost to the base model — bge-large,
mxbai and arctic-l fell to 0.61, 0.61 and 0.59 on the low-overlap queries, the case the
design exists for, against 0.71. Every alternative tried lost on every summary metric
except mxbai's recall@10.

**Selected on the tuning set: bge-base-en-v1.5, variant H** (max over `card`/`summary`
fields, interleaved with lexical, semantic first). It was already in place, so the test
set runs with the configuration the project had before run 3 began, plus the two
retrieval changes above.
mxbai has the best recall@10 (0.92) but ranks the right answer lower. One confound:
the bge-base in use is fastembed's quantized export and bge-large is full precision, so
bge-base vs bge-large is not a pure size comparison. nomic was given its required
`search_query:` / `search_document:` prefixes and was still the weakest by a wide margin.

### The test set, run once

`queries_test.json`, unchanged since `76e6b4e` (verified with `git diff` before the
run). 52 queries, 51 resolvable — `adaptive-metrics-autoapply` has no detected unit, as
in the held-out set. Configuration: bge-base-en-v1.5, `card` + `summary` fields,
`HOMEGROWN_FUSION=interleave`, reranker off. Raw output: `test_run_output.txt`.

| test set (n=51) | recall@1 | recall@5 | recall@10 | MRR | misses |
|---|---|---|---|---|---|
| keyword | 0.43 | 0.69 | 0.78 | 0.54 | 8 |
| semantic | 0.59 | **0.84** | **0.92** | 0.69 | **1** |
| **hybrid (selected)** | **0.59** | **0.82** | 0.88 | **0.69** | 2 |

| by measured overlap | n | keyword@5 | semantic@5 | hybrid@5 |
|---|---|---|---|---|
| low < 0.20 | 23 | 0.48 | 0.65 | 0.61 |
| mid 0.20–0.45 | 21 | 0.81 | 1.00 | 1.00 |
| high ≥ 0.45 | 7 | 1.00 | 1.00 | 1.00 |

**Gate 1: PASS, narrowly. Hybrid recall@5 = 0.82 against a line of 0.80, on a set that
was never used to choose anything.** This is the first gate-1 number in the project that
can be quoted, and it should be quoted with three qualifications:

1. **The margin is one query.** 0.82 vs 0.80 on 51 queries is the difference between 42
   and 41 hits. The confidence interval comfortably spans the line.
2. **The low-overlap stratum is still 0.61** — below the line, and it is the case the
   design exists for. Where the asker shares the asset's vocabulary even a little,
   retrieval is near perfect (1.00 in the mid band); where they share none, it finds the
   right thing about three times in five.
3. **Hybrid did not beat plain semantic here** (0.82 vs 0.84; identical recall@1 and
   MRR). The lexical half contributes no measurable gain at recall@5 on this set. It was
   not removed after seeing this, because that would make the test set a tuning set.

**What run 3 got right, by comparison with run 2:** the tuning-set score for the chosen
method was 0.80 and the test-set score is 0.82 — no dev/test gap this time, against a
13-point gap in run 2. Run 2 picked its fusion method after looking at the result; run 3
made a few principled changes, chose on tuning data only, and committed the test set
before trying anything. The absence of a gap is the evidence that the method was not fit
to the queries.

### Pre-registered ablation: agent keywords

| test set (n=51) | recall@1 | recall@5 | recall@10 | MRR |
|---|---|---|---|---|
| without keywords | 0.59 | 0.82 | 0.88 | 0.69 |
| with keywords | **0.80** | **1.00** | **1.00** | **0.88** |

Low-overlap stratum: 0.61 → 1.00.

**Do not read that as "keywords solve retrieval".** The keywords were written by the same
author who chose the gold assets, and their mean overlap with the gold asset's text is
**0.44** — they leak the answer. Only 3 of 51 queries have keywords that barely touch the
gold text, which is too few to say anything. The honest conclusion is narrower: the
`keywords` input is wired correctly and moves results strongly in the direction the
design predicted, but the size of the effect for an agent that does *not* know the corpus
is unmeasured. Measuring it needs keywords generated blind — by an agent given only the
query.

## Run 4: pre-registration (committed before any run-4 result exists)

Run 3's selected system is frozen: bge-base-en-v1.5, `card` + `summary` fields,
interleaved fusion, no reranker. Run 4 measures three things about it, under these rules:

**4a — blind keywords.** The run-3 keyword ablation was inflated because the author of
the gold labels wrote the keywords. Here the 52 test requirements — text only, no gold
labels, no keywords — go to a fresh LLM that has never seen the corpus or the answers,
which writes the 3-6 keywords an agent would add. The test set is then scored with and
without those keywords, and **both numbers are reported whichever is higher**. This
measures an input of the frozen system; it does not change the system.

**4b — LLM-written purpose lines.** A fresh LLM that has never seen any query writes one
"what is this for" sentence per asset, from that asset's own name, description and README
only. It is stored as a third embedding field (`purpose`). Whether to adopt it is
decided **on the tuning set only** (dev + held-out). If the tuning set says adopt, the
test set gets **exactly one more look**, with the adopted system, and that number is
reported as a second use of the test set. If the tuning set says don't adopt, the test
set is not run again.

**4c — scale.** Recall@5 of the frozen system on the test set as real distractors are
added: public repositories from the same and other organisations, not in the corpus,
indexed from their name and description only. Corpus sizes ~86, ~600 and ~2,500. This
measures degradation of a fixed system and changes nothing.

**How the blind LLM calls were made.** The project's own Anthropic API key had no
credit at the time (`400: credit balance is too low`), so the calls in `src/homegrown/llm.py`
could not run. Both jobs were instead given to fresh Claude subagents, each allowed to
read exactly one input file and write one output file, with no access to the queries or
labels. Blindness here is by instruction and by what the input file contains, not by
enforcement — the subagents had file-system tools available. Their outputs are committed
so the scores can be re-derived without them.

## Run 4: results

### 4c — scale, measured first (frozen run-3 system, test set)

| corpus | what was added | hybrid r@5 | semantic r@5 | low-overlap r@5 | raw output |
|---|---|---|---|---|---|
| 86 | — | **0.82** | 0.84 | 0.61 | `test_run_output.txt` |
| 492 | 406 other Grafana repos | 0.63 | 0.63 | 0.35 | `scale_grafana.txt` |
| 1,307 | + 815 from open-telemetry, prometheus, kubernetes-sigs, hashicorp | 0.59 | 0.59 | 0.30 | `scale_multiorg.txt` |
| 1,909 | + 602 from apache | 0.55 | 0.57 | 0.30 | `scale_2500.txt` |

The pre-registered target was ~2,500; the five orgs yielded 1,909 active, described repos,
and that is what was measured. **Retrieval degrades badly with size, and the damage is
done by near neighbours, not by volume.** The first 406 distractors — Grafana's own other
repositories, the same kind of thing as the gold assets — cost 19 points of recall@5 and
nearly halved low-overlap recall. The next 1,417 from four unrelated organisations cost 8
points more. A real org's corpus is mostly near neighbours, so the 86-asset numbers are
optimistic by a wide margin.

### 4a — blind keywords (test set; both numbers reported, as pre-registered)

| | recall@1 | recall@5 | low-overlap r@5 |
|---|---|---|---|
| 86 assets, without keywords | 0.59 | 0.82 | 0.61 |
| 86 assets, **with blind keywords** | 0.75 | **0.94** | **0.87** |
| 1,909 assets, without keywords | 0.41 | 0.55 | 0.30 |
| 1,909 assets, **with blind keywords** | 0.55 | **0.76** | **0.70** |

Keywords came from a subagent that saw only the requirement text (`blind_keywords.json`).
It reported removing its own first guesses at actual Grafana project names (`augurs`,
`dskit`, `faro`); widely known public tool names (`clickhouse`, `prophet`, `k6`) remain,
as they would for any real agent. Mean keyword/gold-text overlap is **0.37**, against 0.44
for the run-3 author-written keywords. **The gain is real and it comes from the agent's
general knowledge** — translating "columnar warehouse for log analytics" into `clickhouse`,
`OLAP` — not from leaked answers: on the 9 queries whose keywords barely touch the gold
text, the 86-asset score does not move (0.78 → 0.78). At 1,909 assets, keywords recover
most of the scale loss (0.55 → 0.76) — the largest single effect in the project.

### 4b — LLM-written purpose lines

Written by a subagent from each asset's own name, description and README only
(`purpose_summaries.json`; 16 of 86 self-rated low confidence), embedded as field
`purpose`.

**Tuning set (n=106), the decision:**

| | r@1 | r@5 | r@10 | MRR | low r@5 |
|---|---|---|---|---|---|
| frozen (card, summary), hybrid | 0.58 | 0.80 | 0.89 | 0.68 | 0.70 |
| **+ purpose, hybrid** | **0.64** | **0.93** | **0.97** | **0.76** | **0.89** |
| purpose only, semantic | 0.63 | 0.93 | 0.95 | 0.76 | 0.95 |

Pre-registered rule: adopt if hybrid r@5 rises and low-overlap r@5 does not fall.
0.80 → 0.93 and 0.70 → 0.89: **adopted.**

**Test set, its second and pre-registered last look (n=51, 86 assets):**

| | recall@1 | recall@5 | recall@10 | MRR | low-overlap r@5 |
|---|---|---|---|---|---|
| run 3 system | 0.59 | 0.82 | 0.88 | 0.69 | 0.61 |
| **+ purpose lines** | **0.75** | **0.98** | **1.00** | **0.84** | **0.96** |
| + purpose + blind keywords | 0.84 | 1.00 | 1.00 | 0.92 | 1.00 |

One miss out of 51: "derive required reviewers configuration from code ownership" (gold
ranked 10th).

### What that number does not mean

The jump is large enough that its limits matter as much as it does:

1. **The low-overlap stratum has lost its meaning.** Overlap is measured against the
   original indexed text, which does not include the purpose lines — and the purpose
   lines are written in exactly the plain register the queries are. The stratum no
   longer isolates queries that share nothing with what is indexed. That is the
   mechanism working as designed (bridge vocabulary on the document side), but 0.96
   should not be read as "solved the hard case".
2. **Both sides are model-written.** Every query was written by Claude and every purpose
   line by a Claude subagent. Shared phrasing habits — the same plain-language
   paraphrase of "what a thing is for" — plausibly inflate the match. Real developer
   queries are the test this cannot stand in for.
3. **86 assets, and the scale test cannot be rerun fairly yet.** The distractors have no
   purpose lines; adding the field to gold assets only would hand them an extra signal
   their competitors lack. Measuring purpose lines at scale needs them written for all
   1,909 assets — about 1,800 more blind LLM calls.
4. **The test set has now been used twice.** Both uses were pre-registered and the
   second was gated on a tuning-set decision, but it is no longer pristine. A further
   method change needs a fresh query set — ideally written by people, not models.

**Gate 1 is passed** on the terms the design set — ≥0.80 recall@5 on held-out queries —
at the corpus size it was specified for. The open problems that matter next are scale and
real queries, not the retriever.

## Status of the run-1 to-do list

| item from run 1 | outcome |
|---|---|
| A held-out query set | **Done**, and then a third: the test set above |
| A better layer-1 summary extractor | **Done**, kept — it helped |
| A reranker | **Tried and rejected** — four cross-encoders, every one hurt |
| Agent `keywords`, measured separately | **Measured, but inflated** — the keywords leak the answer |
| A larger corpus | **Still open** |

## What is still open

1. **The low-overlap stratum (0.61).** The remaining failure is specific: queries that
   share no vocabulary with the asset. Better embeddings were the obvious lever and
   every alternative model lost, so the next candidates are on the document side —
   richer summaries (an LLM-written one-line "what is this for", stored as a layer-1
   claim with evidence) — rather than the retriever.
2. **Blind keywords.** Have an agent that has never seen the corpus write the keywords
   from the query alone, then re-measure the ablation. That is the realistic version
   of the `find_existing` call, and it is the one number here most likely to be large.
3. **Scale.** 86 assets makes recall@5 easy. The same test at a few thousand assets is
   the number a real organisation would see.
4. **A second rater** for gold labels, and separating the ~20 near-identical `augurs`
   sub-crates, which make some labels genuinely ambiguous.

## Caveats

- 51 queries, one author, no second rater. The overlap bands have small n (4 in the
  high band).
- Author-written queries carry unavoidable vocabulary bias even when deliberately
  avoided; the overlap measurement bounds it but does not remove it.
- The `augurs` repo contributes ~20 near-identical sub-crates, which makes several gold
  labels genuinely confusable. That is realistic for a monorepo, and it is part of why
  recall@1 is low, but it is worth separating in a future run.
