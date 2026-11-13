# Results

Every number here comes from a script in `scripts/` or a recorded run in `experiments/`.
Each test's rule was written down before it ran. Numbers measured on settings that are no
longer the default (the earlier fit and requirements judges, the code-words search list) are
left out.

## Answers, end to end

The product as it ships: query rewrite, BM25 + vector search, the smart judge (Claude Opus)
reading the top 20 results and showing up to 3.

| What | Result |
|---|---|
| **Fresh realistic questions.** 92 questions written blind by six role-playing writers as chat messages, team-channel posts and coding-agent requests, about 29% expected by their writers to have no answer; 5,000 components; run once | First option rated **good 70%**, okay 24%, **bad 6%** (33 rated by one reviewer). No result wrongly labelled "use as-is". Both bad answers were cases where search found nothing close |
| **Judge choice.** 89 goal-style questions, one blind reviewer comparing two judges' lists over the same top 20, 25 questions | Current judge preferred **16 to 3** (6 the same; sign test p = 0.004); first option good / okay / bad 20 / 3 / 2 against 17 / 6 / 2 |
| **Clarifying question.** Same 92 fresh questions | Asked on **25 of 92** (27%): 7 which cloud, 7 which tool or platform, 4 which language, 7 which of two meanings. The reviewer judged them useful and saw no worse first options |

## Search

| What | Result |
|---|---|
| Right component in the top 5, 5,000 components, 108 test questions | **85%**; **92%** with the same questions in casual wording |
| Code reader end to end: 12 public repos crawled by the product into 60 components, 63 feature-level questions | right one in the top 30 (what the judge reads): **62%** tuning / **65%** held-out. The finer splits made in testing reach 69% / 77% |
| Meaning (vector) search, 300k vectors (~100k components), under row-level security | **~0.1 s** with the HNSW index, 1.5 s exact scan (`experiments/scale/search_load_100k.txt`) |
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

- **One reviewer.** The judge and duplicates ratings come from one person. The fresh set was
  run once; 33 of its 92 answers were rated.
- **"Nothing fits" is rare by design.** The judge shows partly matching options labelled
  "related" rather than an empty answer, and said nothing fits on 2 of 92 fresh questions.
  How often it should say so has not been measured on enough questions.
- **The 85–92% uses test profiles** written by AI models during testing, not by the
  product. The code reader's own profiles reach the shortlist less often (above), and it
  has not been run on a real company's code.
- **Recall drops as the org grows.** Look-alike components from other teams are the main
  cause. Search has not been measured beyond 5,000 components.
- **Clarifying questions** were rated as a whole, not one by one, and the second search
  after an answer has not been measured.
- **Duplicates** were tested on public code only, with 9 decisive ratings; 9 of 9 fits a
  true precision as low as about 70%. The judge is the main cost.
- **Who to ask** has only been run against generated people. It needs a directory import
  to name real people and managers; teams loaded from a directory have no lead to fall
  back on yet.
- **Model cost and wait.** Each question makes two model calls (rewrite and judge); the
  judge is one Opus call over 20 cards. Cost and wait have not been measured on live
  traffic, and only a repeated question's rewrite is cached.
- **Evidence quality varies by language.** Test names are the main evidence. JavaScript
  `it("rejects a payload over 1 MB")` says a lot; Python `test_max_age` says less.
- **Not production-hardened.** There is no single sign-on, no syncing of real repo
  permissions, and no real users yet. See [production.md](production.md).
