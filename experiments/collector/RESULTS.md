# The collection and answering agents on real data

Run 2026-11-05. `scripts/collect_replay.py` over the 39 cloned Grafana repositories:
one `reconcile` (backfill) event per repo, each repo's 3 most recent real commits from
the GitHub API, and up to 3 markdown pages under each repo's `docs/`. Everything went
through the real queue and the real graph with a Postgres checkpointer; a simulated
owner made new assets discoverable org-wide and rejected every proposed deprecation.
Raw log: `../collector_replay.txt`.

## What the agent did

| | count |
|---|---|
| events queued | 199 (116 new, 83 skipped as duplicates by idempotency key) |
| **ignored without any model call** | **95 (48%)** |
| applied | 104 |
| assets updated / created | 129 / 23 (all 23 created were documentation pages) |
| assets now carrying contributors | 86 of 86, across 191 pseudonymous people |
| stage | 59 dev, 19 beta, 8 prod, 23 unknown (the doc pages, by design) |

Commits: 74 of 117 ignored (63%). Why events were ignored:

| reason | events |
|---|---|
| bot actor (Renovate, GitHub Actions, release bots) | 53 |
| page does not describe a solution, decision or pitfall | 19 |
| only lockfiles, CI config or generated files changed | 10 |
| no reusable unit touched | 7 |
| dependency bump by a human | 3 |

**Triage works.** Every ignored commit inspected was correctly ignorable, and this is
the stage that keeps cost proportional to signal: half of all events never reach
extraction, and none of them costs a model call.

## What did not work as well, found by inspecting the output

**1. Choosing which documentation pages to keep is weak in both directions.** The rule
is a keyword list (`_SOLVED_SIGNAL` in `collector/graph.py`).

- Kept, but are reference docs rather than solved problems: "Developer Guide", "Usage",
  "Compatibility matrix", "Introduction to the Grafana data structure".
- Ignored, but arguably worth keeping: "Deprecation Policy", "Validating manifests with
  kubeconform".
- Correctly kept: "Migration: generated MCP setup replaced by gcx guidance" — a real
  decision record.

This is the same lesson as the gate-2 verdict: a keyword rule is a poor stand-in for a
judgment. The node has an injectable model decider (`Deps.decider`) that fails safe to
the rules; it needs API credit to run.

**2. Stage inference is right for released products and wrong for internal libraries.**

| asset | inferred | basis | verdict |
|---|---|---|---|
| clock-panel | prod | stable v3.2.3, 10 stable releases | plausible |
| grafana-aws-sdk | prod | stable v1.5.7 | plausible |
| augurs | beta | 0.x release | plausible |
| **dskit** | **dev** | no releases, no version | **wrong**: a core production library used by Loki and Mimir |
| alerting | dev | no releases, no version | likely wrong for the same reason |
| carbon-relay-ng | dev | README contains "not production ready" | likely wrong: its own description says "production ready"; the regex matched a sentence about something narrower |

The dskit case matters most. Internal libraries are typically consumed by commit hash or
pseudo-version, not by release, so **in a real organisation the release signal would
mislabel most shared libraries as dev**. The better signal is the one the design already
lists as a card field — usage: *who depends on it*. A library imported by services that
are in production is itself in production. Until that is built, stage stays marked
"inferred, not confirmed", which the answering agent prints on every answer.

**3. Names.** Go module paths end in a major version (`.../crossplane-provider-grafana/v2`)
and the display took the last segment, so one asset was shown as `v2`. Fixed
(`models.display_name`).

### Follow-up: stage from usage (fixes the dskit case)

`collector/usage.py` reads `go.mod` requires and `package.json` dependencies in every
checkout, writes `depends_on` edges between assets with the manifest as evidence, and
lifts an unconfirmed stage to prod when two or more other teams depend on the asset, or
a prod asset does. It only lifts, never lowers, and never touches an owner-confirmed
stage. `// indirect` Go requires are transitive, not users, and are not counted.

On the corpus: 62 published module/package names, 8 direct cross-asset dependencies.
**dskit: dev -> prod, "depended on by 2 other teams"** (alerting, authlib). The answering
agent now prints *used by* for each match — another "who to ask" signal — and, because
the edge policy needs both ends visible, never names a dependent the asker cannot see.
Eight edges across 39 repos is thin; a real organisation's dependency graph is far
denser, which is when this signal does most of its work.

## The answering agent, three real questions

Asked as a developer who may discover every team's assets but read none of their code.

**"collapse request paths that contain ids into route templates…"** → `clusterurl`
first: owning team, LOB, manager, its three main contributors with commit counts and
last activity, stage beta (from v0.3.0, unconfirmed), and "request access from Lead,
team-clusterurl". Second result, `carbon-relay-ng`, is a false match.

**"shared AWS credential and region handling for our plugins"** → `grafana-aws-sdk`
first, prod (v1.5.7). Its main contributors sit in *team-athena-datasource*, not the
owning team — a cross-team contribution that the real commit history surfaced, and
exactly the "who to ask" signal the answer exists for. Two further results are false
matches.

**"convert a temperature from celsius to fahrenheit"** (nothing in the org does this) →
"Possibly: clickhouse-datasource". **Wrong.** The conservative wording held — rule
verdicts can only say "possibly", never "yes" — but irrelevant matches are still listed.
This is the rule-based verdict's known failure (false alarms on 8/8 held-out control
tickets, `experiments/gate2/RESULTS.md`); the blind model judge had 0/8 on similar
tickets. `ORGMEM_VERDICT_MODE=model` is the fix and needs API credit.

## Summary

The pipeline is sound end to end on real events: idempotent intake, cheap triage that
removes half the volume, attribution to team / LOB / manager, the owner gate, and
answers scoped by row-level security. The two judgment calls — *is this page worth
keeping* and *does this candidate actually fit* — are where the rule-based stand-ins
fail, and both have a model path ready behind a fail-safe. The stage signal needs a
dependency graph, not better rules.
