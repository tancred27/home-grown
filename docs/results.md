# Results

Every number here comes from a script in `scripts/` or a recorded run in `experiments/`.
Each test's rule was written down before it ran. Numbers measured on settings that are no
longer the default, or on question sets later found not to match how people ask (how-to
questions written from release notes, sets never reviewed), are left out.

## Answers (test harness)

These ran in a test harness, not through a live installation. The harness uses the product's
rewrite and judge prompts (Claude Opus reading the top 20 results and showing up to 3) and
the same search method. The 5,000 component descriptions were written by AI models from
README files, each with a list of capabilities. The collector did not write them.

| What | Result |
|---|---|
| **Fresh realistic questions.** 92 questions written blind by six role-playing writers as chat messages, team-channel posts and coding-agent requests, about 29% expected by their writers to have no answer; 5,000 components; run once | 33 first options rated by one reviewer: **23 good, 8 okay, 2 bad** (70% / 24% / 6%). 8 of the 33 were labelled "use as-is"; the reviewer rated all 8 good. For the 10 okay or bad answers, agents searched all 5,053 descriptions by keyword and found nothing the reviewer judged closer |
| **Judge choice.** 89 goal-style questions, one blind reviewer comparing two judges' lists over the same top 20, 25 questions | Current judge preferred **16 to 3** (6 the same; sign test p = 0.004); first option good / okay / bad 20 / 3 / 2 against 17 / 6 / 2 |
| **Clarifying question.** Same 92 fresh questions | Asked on **25 of 92** (27%): 7 which cloud, 7 which tool or platform, 4 which language, 7 which of two meanings. The reviewer judged them useful and saw no worse first options |

## Search

| What | Result |
|---|---|
| Meaning (vector) search step, 300k vectors (~100k components), under row-level security, one user at a time | median **77 ms**, 95% under 176 ms with the HNSW index; 1.5 s exact scan (`experiments/scale/search_load_100k.txt`) |
| A general re-ranking model (bge-reranker-v2-m3) over the top 100 | Made the shortlist worse: 11 of 33 rated first options left the top 20. Not used |
| Two extra query phrasings, merged | No clear change (1 of 33 rated first options left the top 20). Not used |
| Keyword (BM25) search, indexed (`db/013_bm25_index.sql`), 20,000 synthetic components | 41–194 ms typical, 567 ms for a query of only very common words |

## Duplicates across teams (opt-in)

5,053 public components, one repo counted as one team. Known look-alikes: 20 cross-team
pairs that a human-checked answer key credits for the same request.

| What | Result |
|---|---|
| Known pairs reaching the candidate list (search plus capability labels) | **20 / 20**: 10 by search only, 9 by both, 1 by labels only |
| Known pairs the judge marks same or overlapping | **20 / 20** |
| "Same" pairs from 40 random components confirmed by a reviewer | **9 / 9**, plus 11 / 11 from the known-pair components |
| Model tokens per 1,000 components | labels (Haiku) about 0.4M; judge (Opus) about 3.6M in, 0.8M out |

## Collection

| What | Result |
|---|---|
| Freshness: one hour of all public GitHub pushes replayed (111k pushes to main) | change to processed: p95 **225 s**, max 509 s (`experiments/sync/RESULTS.md`) |
| Docs pages worth keeping, 43 hand-labelled pages | LLM: precision 0.60, recall 0.92; keyword rule: 0.26 / 0.46 (`experiments/pages/RESULTS.md`) |

## Known limits

- **Not yet run through a live installation.** The answer tests above are harness results.
  The product's own path (collector writes the components, a user asks) has only been run on
  a 5-repo smoke test.
- **The code reader is untested on realistic questions.** Its components have only been
  scored on how-to questions that do not match the product's goal, so no number is given.
  Until 2026-11-13 the judge was not sent a crawled component's capabilities; it is now.
- **One reviewer, small counts.** One person rated 33 of the 92 answers; 70% good is within
  about 16 points either way. Only 8 rated answers were labelled "use as-is".
- **An exact match was rare in the test data.** The reviewer found an option to adopt as it
  is in 8 of 33 answers; the other 25 were partly useful. Public repositories from many
  companies are not one company's code.
- **"Nothing fits" is rare by design.** The judge shows partly matching options labelled
  "related" rather than an empty answer, and said nothing fits on 2 of 92 questions.
- **Search has not been measured beyond 5,000 components**, and not on a real company's code.
- **Clarifying questions** were asked more often than the limit set beforehand (27% against
  25%) and were kept by the owner's decision. They were rated as a whole, and whether
  answering one improves the result has not been measured.
- **Duplicates** were tested on public code only, with 9 rated groups. "Same job" was
  defined to include units for a different language or cloud; that definition is under
  review. The judge is the main cost.
- **Who to ask** has only been run against generated people. It needs a directory import
  to name real people and managers; teams loaded from a directory have no lead to fall
  back on yet.
- **Stage (dev, beta, prod)** is inferred; its accuracy has not been measured.
- **Model cost and wait.** Each question makes two model calls (rewrite and judge). The
  time and cost of a full answer have not been measured.
- **Not production-hardened.** There is no single sign-on, no syncing of real repo
  permissions, and no real users yet. See [production.md](production.md).
