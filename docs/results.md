# Results

Every number here comes from a script in `scripts/` and a write-up in `experiments/`.
Where it mattered, choices were made on a tuning set and confirmed once on a held-out
test set.

| What | Result | Source |
|---|---|---|
| Finding the right component, 5,000 assets, 108 test questions | right one in the top 5: **85%**; **92%** with the same questions in casual wording (previous search: 79% / 88%) | evaluation write-up not published yet |
| Real developer questions: 69 Stack Overflow titles whose accepted or upvoted answer links one of the 5,000, search plus judge | right one (or another acceptable one) in the top 5: **81%** (95% range 70–89%), top 1: 72%. On the 41 where a blind reviewer agreed the linked project is the answer: **100%** (91–100%). Most misses never reach the judge's top 30 | evaluation write-up not published yet |
| Code reader, end to end: 12 public repos crawled by the product into 60 components; 63 questions about specific features, both wordings, search only | right one in the top 30 (what the judge reads): **62%** tuning / **65%** held-out; top 5: 50% / 38%. One profile per repo from the same code: 61% / 62%. The finer splits made in testing (85 components): 69% / 77% | same |
| Code words as a third search list, 12 public repos plus the 321 repos most often confused with them, all with code; 196 fresh questions | right component in the top 5: **55% → 66%** (product's units) and **58% → 66%** (experiment's units); top 30: 80% → 86% and 84% → 90% | evaluation write-up not published yet |
| Judge, 89 goal-style questions written without seeing the corpus (5,000 assets); one reviewer, blind, comparing two judges' answers for 25 randomly ordered questions each | smart judge over the top 20 against a requirement-marking judge, both over the top 20: list preferred **16 to 3** (6 the same; sign test p = 0.004); first option good / okay / bad **20 / 3 / 2** against 17 / 6 / 2. Earlier, the requirement judge over 20 results against 5: preferred 15 to 4, first option good 20 against 15 | evaluation write-up not published yet |
| Same 89 questions, model-written answer key | best option at rank 1: **54 of 63** answerable with an Opus requirement judge (53 Sonnet), against **37** for search order alone. The key agrees with the human reviewer on only 52–60% of individual cards, so read these as direction, not size | same |
| Fit judge, 30 duplicate tickets + 26 "nothing fits" controls, 109 assets | every findable duplicate caught; **0/26** false alarms | `experiments/scale/RESULTS_judge.md` |
| Same at ~9,600 assets | every findable duplicate caught; 3/26 false alarms | same |
| Meaning (vector) search latency, 300k vectors (~100k components), under row-level security. Keyword (BM25) search, indexed (`db/013_bm25_index.sql`): 41-194 ms typical, 567 ms worst at 20,000 synthetic assets (0.8-1.75 s before the index) | **~0.1 s** (index), 1.5 s (exact scan) | `experiments/scale/search_load_100k.txt` |
| Freshness, one hour of all public GitHub pushes replayed (111k pushes to main) | change to processed: p95 **225 s**, max 509 s | `experiments/sync/RESULTS.md` |
| Docs pages worth keeping (43 hand-labelled pages) | LLM: precision 0.60, recall 0.92; keyword rule: 0.26 / 0.46 | `experiments/pages/RESULTS.md` |

## Known limits

- **The 85–92% uses test profiles.** Its component profiles (capabilities, search terms,
  example questions) were written by AI models during testing, not by the product.
- **The code reader splits too coarsely.** On 12 public repos it kept 3 repos whole and
  made 60 components where testing made 85, and recall was lower (above). It has not
  been run on a real company's code.
- **Short real questions are harder.** Stack Overflow titles score 81% against 85–92% on
  the test questions; search, not the judge, loses most of them.
- **Recall drops as the org grows.** Look-alike components from other teams are the main
  cause. The current search has not been measured beyond 5,000 assets.
- **Keyword search time grows with the corpus.** With the indexes of `db/013_bm25_index.sql`,
  BM25 over asset text and claims takes 41-194 ms for typical queries and 567 ms for a query of
  only very common words at 20,000 synthetic assets (`scripts/bm25_load.py`); before them,
  0.8-1.75 s. Not yet measured at 100,000.
- **Code-words search time grows with the corpus.** With the inverted index
  (`db/012_code_terms.sql`), 20,000 synthetic units of 1,500 words each take 75-106 ms for
  typical queries and up to 340 ms for a query made only of very common words
  (`scripts/code_words_load.py`). It has not been measured at 100,000 components.
- **Clarifying questions are lightly tested.** On 92 fresh questions the judge asked on 25
  (27%), mostly which cloud, tool or language. Whether those questions are useful has not been
  rated yet, and the re-run after an answer has not been measured.
- **LLM cost.** Each question makes two model calls (rewrite and judge). Only a repeated
  question's rewrite is cached.
- **Evidence quality varies by language.** Test names are the main evidence. JavaScript
  `it("rejects a payload over 1 MB")` says a lot; Python `test_max_age` says less.
- **The judge results are from one reviewer and one question set.** Each comparison had 25
  answered questions, and the 89 questions were used for about fifteen design decisions,
  so some of the gain may be fitted to them. A fresh question set has not been run yet.
- **The smart judge's "nothing fits" hasn't been measured.** The 23 of 26 above used the
  earlier judge; the reviews above covered questions that had an answer.
- **Judge cost and wait are not measured.** The default judge is one Opus call over 20
  cards per question.
- **Not production-hardened.** There is no SSO, no syncing of real repo permissions,
  and no real users yet. See [production.md](production.md).
