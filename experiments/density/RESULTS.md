# Experiment E: are boundary and outcome claims extractable from discussions?

Run 2026-11-05. This is step 1 of the build order in `docs/SCOPE.md`: the whole project
rests on the assumption that issue and pull-request discussions contain the knowledge
code cannot give you — where something breaks, what it refuses to do, what happened
when another team tried it. If that knowledge is too sparse to mine, the memory can
only hold what grep already finds and the idea collapses.

## Method

Corpus: five repositories in one organization (`grafana/grafana`, `loki`, `tempo`,
`mimir`, `k6`) — separate codebases, one org, overlapping concerns, a reasonable
stand-in for a siloed org. 3 pages x 100 comments each via the flat
`/repos/{org}/{repo}/issues/comments` endpoint, sorted newest first. 1,500 comments,
all ids distinct.

Two-stage measurement, because a keyword screen alone proves nothing:

1. A loose phrase screen (`screen.py`) over boundary, outcome and alternative signals.
2. **Manual judgement of samples** — 29 flagged and 14 unflagged comments read and
   classified by hand, to get the screen's precision and recall rather than assuming it.
3. A tightened screen (`stats.py`) built from what survived that reading, used for the
   thread-level and length-bucket numbers.

Reproduce: `./fetch.sh <fresh-dir>` then `python -I screen.py <dir> 30 15` and
`python -I stats.py <dir>`.

## What the corpus actually looks like

| | count | share |
|---|---|---|
| comments fetched | 1,500 | |
| **bot-authored** | **1,026** | **68%** |
| human, non-empty | 474 | 32% |
| after exact-body dedup | 385 | |
| distinct threads touched | 324 | |
| comments per thread *in this sample* | 1.46 | |
| median human comment length | 185 chars | |

`github-actions[bot]` alone wrote 637 of 1,500. A cheap deterministic filter before any
model call is not an optimization, it is 68% of the corpus.

## Screen quality, measured by hand

Loose screen flagged 52 of 385 (13.5%). Of 29 read:

| judgement | n | share |
|---|---|---|
| **strong** — a genuinely reusable boundary/outcome/pitfall claim | 12 | 41% |
| thin but real — lifecycle pointers ("Migrated to…", "Superseded by…") | 9 | 31% |
| noise — feature proposals, volunteering, CLA problems | 8 | 28% |

Of 14 unflagged comments read, **0 contained a strong claim** that the screen missed.
It does systematically miss thin resolution pointers ("fixed in 3.0.4", "done as of
#15863"), which are worth adding.

Nine of the thin hits were the same mechanical event — `Migrated to <url>` notices from
one repository split — which inflated loki's raw rate from 2.2% to 23.8%. Boilerplate
has to be stripped before any density claim.

## Density

Tightened screen, boilerplate removed:

| measure | value |
|---|---|
| strong-signal comments | 19 / 474 human = **4.0%** |
| threads with ≥1 strong signal | 15 / 324 = **4.6%** |

Per repository: tempo 7.8%, k6 3.3%, grafana 2.8%, loki 2.2%, mimir 2.1%. A 3.5x spread
across one organization — corpus choice matters more than expected.

## The useful finding: length is a strong, free prefilter

| comment length | comments | strong | rate |
|---|---|---|---|
| 0–100 | 199 | 1 | 0.5% |
| 100–250 | 91 | 1 | 1.1% |
| 250–500 | 75 | 2 | 2.7% |
| 500–1,000 | 48 | 7 | 14.6% |
| 1,000–2,500 | 26 | 4 | 15.4% |
| 2,500+ | 12 | 4 | 33.3% |

**Comments ≥500 chars are 18% of the human corpus and hold 79% of strong signals.**
Monotonic across every bucket. That is a language-independent filter requiring no model
call, and it cuts the model-call budget roughly 5x at a 21% loss of signal. Combined
with bot removal, 1,500 raw comments reduce to 86 worth sending to a model.

## The claims that exist are high quality

This matters more than the rate. A sample of what was recovered, each with a URL for
evidence:

- Concrete limits: ingester flushes failing on objects over 16 MiB; an append past
  5 GiB now failing immediately rather than buffering.
- Operating envelope: "the avg when flushing is more like 20ms" versus a reporter's
  340 ms setup, with the explicit judgement that the reported thresholds apply only to
  that setup.
- Scope honesty: "this came from scale testing with many small tenants; we have not
  observed it as an operational limitation in a live deployment."
- A mitigation declared insufficient: disabling MPTCP "provides a wrong sense of
  protection… the issue is in the TCP stack," with the real fix and a workaround.
- Deliberate non-coverage: a list of handlers covered and handlers that "don't need it,"
  with reasons.
- Capability plus the precise gap: "Tags and groups already covered most of it… the
  missing piece is attaching a high-cardinality key without creating a new time series."
- **A duplicate-work event with its reason**, found in a random sample of 29: a pull
  request closed because "the offset is already fixed on main by #136824… this changed
  the lookup instead of replacing those tables, so it is no longer needed."

None of this is derivable from source code. The premise holds on quality.

## Verdict

**The premise survives, with one correction and one warning.**

The knowledge is real, specific, evidence-linked and exactly the kind code cannot
supply. The rate is lower than hoped — roughly 1 strong claim per 25 human comments —
but the absolute yield is what matters. For an organization with ~50,000 comments
across its repositories: ~16,000 human, ~2,900 long enough to be worth a model call,
yielding several hundred evidence-backed claims for a few dollars of inference. That is
a usable memory, not a trickle.

**Correction to the plan:** ingestion is a funnel, not a sweep. Bot filter, then length
gate, then model. Budget by long human comments, not by comments.

**Warning — this sample is biased and 4.0% is a floor.** Sorting newest-first returns
in-flight pull-request mechanics (1.46 comments per thread means a thin slice of many
threads, not deep discussion). The strong claims all came from threads with real
back-and-forth. A targeted sample — closed issues with ≥5 comments, and the
`wontfix` / `not planned` / `by design` labels, which are boundary declarations by
definition — should be materially richer. **That is the next measurement**, and it needs
an authenticated token for the budget.

## Incidental finding: discussions contain personnel data

One sampled comment read: "Unassigning myself before the vacation… the first thing now
is setting up the recruitment with @… before she leaves for offsites next week." Issue
threads carry absence, performance and hiring information. Ingestion must not let this
reach a card. Add it to the redaction rules in `docs/design.md` section 8 — it was not
on the leak-path list, which only considered private *code*.

## Caveats

- One organization, one sampling strategy, recent comments only.
- Judgements are mine, one rater, no second opinion. The strong/thin boundary is a
  judgement call and 29 samples give a wide confidence interval.
- `screen.py` dedupes by exact body (385) while `stats.py` does not (474). Densities
  above use 474 as the denominator, which is the conservative choice.
- A keyword screen cannot find claims phrased without its phrases. Recall on strong
  claims looked high (0 missed in 14) but that is a small sample.
