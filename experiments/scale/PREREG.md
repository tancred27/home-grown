# Scale test with purpose lines for everyone: pre-registration

Written and committed 2026-11-07, before any purpose line for the distractors exists.

## Question

Does retrieval hold up at ~1,900 assets once **every** asset, distractors included, has a
purpose line? Earlier scale numbers were unfair in one direction or the other:

- gate-1 run 4c: nobody had purpose lines → recall@5 0.55 at 1,909 (gate-1 test set);
- gate-2 gold ranks: real assets had README-based Groq lines, distractors had none
  → 27/30 in the top 5 at 1,932 (flattered).

## Inputs

- 1,909 items: the 86 real units plus 1,823 distractor repos (the same five extra orgs and
  Grafana's other repos as run 4c: grafana, open-telemetry, prometheus, kubernetes-sigs,
  hashicorp, apache; active and described only).
- Each item is **name + description only**: the GitHub description for distractors, the
  stored summary for real units. Same writer, same input type for everyone.
- Shuffled, with opaque ids (`x0001`…), so the writer can't tell real from distractor.
  The id→asset key is kept in a separate file the writer never reads.
- Writer: a fresh Claude instance (Claude Code subagent) that sees only those input files
  and the product's purpose-line instruction (`collector/purpose.SYSTEM`). It never sees
  queries, tickets or labels. I (the orchestrating session) have seen the tickets, so I
  don't write the lines myself.

## Conditions

All are measured inside **one database transaction that is rolled back**, so the live
database is unchanged. Searches run as `orgmem_app` with the gate-2 dev user under RLS,
exactly as in the product.

| | corpus | real units' purpose field | distractors' purpose field |
|---|---|---|---|
| A0 | 109 (today) | Groq, README-based (production) | — |
| A | 1,932 | Groq, README-based | none (= the earlier, flattered run) |
| C0 | 109 | Claude, name+description | — |
| **C** | **1,932** | **Claude, name+description** | **Claude, name+description** |

**C vs C0 is the headline**: the cost of scale when everyone competes on equal terms.

## Metrics, fixed now

1. gate-1 test set (`experiments/gate1/queries_test.json`, without keywords):
   recall@1, recall@5, recall@10.
2. gate-2 duplicate tickets (30, all four task sets): gold in top 1 / 5 / 15 / 30.

No method changes are made on the basis of these numbers within this run. The gate-1 test
set has been used before for measurement (see gate1/RESULTS.md caveat 4). It is used here
only to measure, never to tune.

## Addendum, 2026-11-07, before any code-signal line exists: condition K

READMEs are often outdated, so a map built from them describes what the code used to do.
Condition **K** writes every purpose line from **code-derived signals only**: name,
one-line description, language, manifest module + imported dependencies, directory
names, source file names and test file names (`collector/signals.signal_text`, capped at
1,200 characters). No README. Distractors' signals come from the GitHub tree + root
manifest (`scripts/fetch_code_signals.py`). Real units get the same function on their
local checkout. Same opaque ids, same blind-writer setup (fresh Claude instances; they
see only the signal files). The instruction adds one sentence: "The one-line description
may be outdated; where it disagrees with the code signals, trust the code signals."

Measured exactly like C / C0, as conditions **K0** (109 assets) and **K** (1,932).
Decision rule, fixed now: if K0 is within 0.05 of A0 on gate-1 test r@5 **and** K beats
C, the production map switches to code signals as the primary input, with the README
demoted to an optional, explicitly-possibly-stale extra.
