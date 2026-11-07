# Which documentation pages should become memory? Keyword rule vs LLM decider

Run 2026-11-06. `scripts/page_eval.py`, model `openai/gpt-oss-120b` on Groq, pinned for
the run, every call cached in `judge_cache_groq.json`, full output in `eval_groq.json`.

**Labels** (`labels.json`): the 43 real documentation pages from the collector replay,
hand-labelled by one rater **before the decider existed**, with a written criterion:
KEEP if another team would want the page before building something (a solution or
approach, a design decision, a policy, a limitation or compatibility boundary, a
migration guide, a reusable how-to or runbook); IGNORE usage and reference pages for one
tool's own features, contributor or release-process docs, and pages that only restate
what the asset is. 13 of 43 are KEEP.

The decider's prompt (`src/homegrown/collector/pages.py`) states the same criterion. So this
measures whether the model applies the rule the way the rater did. It does not show the
rule is the right one.

## Result

| | kept | right | wrong | missed | precision | recall | agreement |
|---|---|---|---|---|---|---|---|
| keyword rule (`_SOLVED_SIGNAL`) | 23 | 6 | 17 | 7 | 0.26 | 0.46 | 0.44 |
| **LLM decider** | 20 | 12 | 8 | **1** | **0.60** | **0.92** | **0.79** |

0 failed calls. The decider is now on in the collector (`Deps.use_llm_pages`), and it
falls back to the keyword rule if the model fails.

## Where it disagrees with the labels

**One miss:** "Exploring Grafana events". The model read it as a list of one tool's
events, which is a defensible reading.

**Eight extra keeps, almost all of one kind:** tool-specific how-tos ("Amazon Athena
alerting", "ClickHouse alerting", "Creating a codegen pipeline", "Applying builder
transformations"), two dashboard-linter rule pages, a conceptual overview ("Data
frames") and an "input" page. The model calls these reusable how-tos or policies. The
rater called them one tool's usage docs. The boundary between the two is fuzzy, and a
second rater might well side with the model on several of them.

For a memory, this is the cheaper direction to err. An extra page costs a little noise
in search, but a dropped decision record is lost.

## Not done, on purpose

The prompt was **not** tuned against these labels. With 43 pages and no held-out set,
any tuning would just fit the score. A prompt change needs a fresh, separately labelled
page set, ideally labelled by more than one person.

## Cost

About 1–1.5k tokens per page on the free tier (200k tokens/day, refilling at about 140
per minute). The collection agent only calls the decider for pages that pass triage
(not meeting notes, not very short).
