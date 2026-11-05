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

## The external-validity problem (raised 2026-11-05, unresolved)

**The corpus is five popular public repositories, where discussion is forced by
distributed collaboration with external contributors. Private internal repos may never
hold these conversations at all** — colleagues talk in Slack, in standups, and in
person, and shared context means less gets written down. So 4.0% is *not* simply a
floor. Two biases push opposite ways and the net direction is unknown:

- Sampling newest-first returned in-flight PR mechanics (1.46 comments/thread) →
  **underestimates**.
- Choosing popular OSS with a high write-down culture → **overestimates** for internal
  repos, possibly by more.

What the literature says, such as it is:

- Closed-source review comment ratio **9–10% vs 13–26% for open-source** (two
  industrial projects vs three OSS datasets), attributed partly to larger patches:
  https://dl.acm.org/doi/pdf/10.1145/3660806 — a 1.5–3x discount, so ~2% internally.
- But Rigby's open/closed comparison found "little difference" between review practices.
- And **64.7% of OSS PRs are reviewed with zero comments** (LGTM-smells study), so
  public repos are much less discussion-rich than this Grafana sample implies either.
- Google (closed source) reaches 12.5 comments per change on ~1,250-line changes.
- Nobody has measured boundary/outcome claim density in private vs public repos. The
  tacit-knowledge literature on this is entirely vendor blogs; one claims "80% of
  operational intelligence is tacit" with no source. Do not cite it.

### Run E2: the gradient, measured (2026-11-05, authenticated)

Eight `grafana/*` repos with 0–1 stars, actively pushed, as a proxy for internal:
`grafana-tempo-datasource`, `grafana-loki-datasource`, `grafana-canvas-panel`,
`cloud-setup`, `grafana-pyroscope-datasource`, `xk6-kafka`, `pyroscope-kinda-safe`,
`grafana-opentsdb-datasource`. Every one **exhausted its entire comment history** in
under 500 comments.

| | flagship (5 repos, recent slice) | effectively-internal (8 repos, full history) |
|---|---|---|
| comments fetched | 1,500 | 843 |
| bot-authored | 68% | **58%** |
| human, non-empty | 474 | 358 |
| distinct threads | 324 | 157 |
| comments per thread | 1.46 | 2.28 |
| **strong claims** | **19 (4.0%)** | **19 (5.3%)** |
| threads with ≥1 claim | 4.6% | **9.6%** |
| share of signal in comments ≥500 chars | 79% | **37%** |
| claims per repo | — (not exhausted) | **~2.4, for the repo's entire life** |

**Density does not degrade. It is slightly better.** 5.3% vs 4.0% per comment, and 2x
better per thread. Bot noise is lower. Manual read of 14 flagged comments gave ~50%
strong, ~21% thin, ~29% noise — precision comparable to or better than flagship.

**Quality holds too**, and one claim type is *denser* here than in the flagship repos:
"this already exists" and "is this a duplicate of X" — the exact duplication-discovery
signal the product is built on. Samples recovered:

- A capability relocation with a cost judgement: "since the GATB migration, the matrix
  build no longer lives in this repo's `ci.yml` — it's now inside
  `grafana/plugin-ci-workflows`… a local workaround is possible via the existing
  `playwright-gar-registry` input but needs new GAR credentials/Vault setup — cost
  likely outweighs benefit for an image this small."
- A root-caused, generalised CI boundary: "GitHub doesn't issue an OIDC token for
  workflows triggered from a fork… **any fork PR should hit the same error.**"
- A performance boundary with no escape: "Loki is not performing well with high
  cardinality, there doesn't seem to be a good workaround."
- A workaround with the exact recipe: "adding a parser like `| logfmt |` in the query
  for the ad hoc to work correctly. This is kinda weird workaround, but it works."
- "I believe this already exists in Grafana… under Additional Settings I have these
  options" — a duplicate feature request caught by a human.

**What collapses is volume, not density.** Eight repos' *entire lifetimes* produced 19
strong claims — about 2.4 per repo, ever. The five flagship repos produced 19 from a
recent slice alone. And the distribution is brutally skewed: `grafana-tempo-datasource`
(9) and `grafana-loki-datasource` (7) hold 16 of 19, while **four of eight repos have
zero.**

**Correction to run E1: the length prefilter does not generalise.** Comments ≥500 chars
hold 79% of signal in flagship repos but only **37%** here, and short comments carry
real density (2.4% in the 0–100 bucket vs 0.5% in flagship). Using length as a 5x budget
cut would discard 63% of the signal on internal-style repos. Keep the bot filter, which
holds in both; make the length gate configurable and measure it per corpus.

**What this does not settle.** These are *public* repos with *external users filing
issues*, so the proxy is partial — low popularity and low external contribution, but
not zero outside reporters. Reading the authors, roughly half the rich claims came from
people who look like external users rather than employees. A genuinely private repo has
none of those, which could halve the yield again. Settling that needs real private data,
which this project cannot use. **It stays a declared assumption, not a measured fact.**

### Design consequences if internal density really is near zero

1. **Sources that do not depend on discussion culture.** Tests are the strongest:
   `rejects_payload_over_1mb` *is* a boundary claim, it is machine-readable, it exists
   even in silent teams because CI demands it, and unlike prose it stays true or the
   build breaks. Then code comments written at the moment of pain (`// don't use this,
   use X`, `// breaks above 1MB`, TODO/FIXME/HACK), deprecation markers, and
   postmortems, which orgs mandate even when they document nothing else. None of these
   were in the plan.
2. **The memory must elicit, not only extract.** The design had reuse outcomes
   "reported by people", noted as unlikely to bootstrap. Under this objection it becomes
   mandatory, and *when to ask without being annoying* becomes a core product problem.
3. **Unaffected:** the access-boundary moat. A bare artifact card — this exists, here is
   the owner, request access — is valuable when the alternative is not being able to see
   the repository at all. This question shifts the weight between artifact-shaped and
   experience-shaped records; it does not decide whether the system is worth building.

### Run E3: what if there are no discussions at all? (2026-11-05)

The hardest version of the objection. Many internal repos have no issue discussion
whatsoever: code is pushed, PRs get "LGTM", tickets live in Jira, conversation happens
in Slack. So: take the repos from E2 that produced **zero** discussion claims and see
what is recoverable from the code alone. Five repos shallow-cloned, 375 code files.

`code_claims.py` counts four discussion-independent sources.

| repo | code files | tests | **boundary tests** | skip-with-reason | TODO-style | **strong warnings** | deprecations |
|---|---|---|---|---|---|---|---|
| cloud-setup | 76 | 5 | 0 | 0 | 0 | 0 | 0 |
| grafana-canvas-panel | 178 | 406 | 45 | 0 | 53 | 4 | 0 |
| grafana-pyroscope-datasource | 71 | 140 | 13 | 0 | 7 | 1 | 0 |
| pyroscope-kinda-safe | 4 | 14 | 2 | 0 | 2 | 0 | 0 |
| xk6-kafka | 46 | 82 | 21 | 0 | 0 | 0 | 0 |
| **TOTAL** | **375** | **647** | **81** | **0** | **62** | **5** | **0** |

**86 discussion-independent candidates against 2 claims from all of these repos'
discussion — roughly 40x more.**

**Tests are the real source, and they are higher precision than discussions ever were.**
Of 9 sampled boundary tests, 8 were genuine capability facts (~89%, against 41–50% for
the discussion screen) — because a test name exists to describe one specific behaviour,
so it is structured by convention rather than by someone's mood:

- "returns an empty assumed dimension when field mode is used but the field is not in
  the frame"
- "should clamp `copy.fixed` to `options.min` when below range and no field configured"
- `TestConsumeAfterCloseErrors`, `TestAdminRejectsInitContext`
- "escapes double quotes in label name on empty input"

Only "should render without error" was noise. And unlike prose, these stay true or the
build breaks.

**Code comments turned out to be nearly worthless, which contradicts run E2's
speculation.** 62 TODO/FIXME-style comments yielded 5 strong hits, all implementation
notes rather than capability boundaries ("this is a workaround to apply styles after the
size update"). Skip-reasons and deprecation markers were entirely absent — plausibly an
artefact of young TypeScript/Go plugin repos; worth re-checking on a mature internal
library.

### What this means: three layers, not one

| layer | source | availability | answers |
|---|---|---|---|
| Artifact | code, manifests, ownership | **always** | what exists, what its API is, who owns it |
| Behavioural boundary | **tests** | **nearly always** — CI enforces it | what it handles, what it refuses, at what limits |
| Experience | discussions, people | **scarce and uneven** | who tried it, who gave up, why, access friction, cost/benefit |

"No discussions" costs the third layer only. Two of three survive, and the middle one is
the higher-precision of the two measured. Tests can answer *does this fit my
requirement*; they cannot answer *did anyone try this and regret it*.

Note also that "no discussions in the repo" usually means "discussions in Jira, Confluence
or Slack" — an adapter problem, not an absence. The Confluence adapter in `design.md`
section 4 exists for exactly this and was cut from the POC for public-data reasons, not
because it is unimportant in a real org.

**Design consequence — demand-driven elicitation.** Where the third layer is genuinely
absent, do not try to fill it up front. A developer asking about a capability *is* the
signal that it matters, so use that question to justify interrupting its owner exactly
once. The memory then warms where it is actually used, and cost scales with demand
rather than with corpus size.

## Caveats

- One organization, one sampling strategy, recent comments only. See the
  external-validity section above — this is the largest limitation of the result.
- Judgements are mine, one rater, no second opinion. The strong/thin boundary is a
  judgement call and 29 samples give a wide confidence interval.
- `screen.py` dedupes by exact body (385) while `stats.py` does not (474). Densities
  above use 474 as the denominator, which is the conservative choice.
- A keyword screen cannot find claims phrased without its phrases. Recall on strong
  claims looked high (0 missed in 14) but that is a small sample.
