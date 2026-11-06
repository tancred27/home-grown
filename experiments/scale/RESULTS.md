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
