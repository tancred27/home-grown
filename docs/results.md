# Results

Every number here comes from a script in `scripts/` and a write-up in `experiments/`.
Where it mattered, choices were made on a tuning set and confirmed once on a held-out
test set.

| What | Result | Source |
|---|---|---|
| Finding the right component, 5,000 assets, 108 test questions | right one in the top 5: **85%**; **92%** with the same questions in casual wording (previous search: 79% / 88%) | evaluation write-up not published yet |
| Real developer questions: 69 Stack Overflow titles whose accepted or upvoted answer links one of the 5,000, search plus judge | right one (or another acceptable one) in the top 5: **81%** (95% range 70–89%), top 1: 72%. On the 41 where a blind reviewer agreed the linked project is the answer: **100%** (91–100%). Most misses never reach the judge's top 30 | evaluation write-up not published yet |
| Code reader, end to end: 12 public repos crawled by the product into 60 components; 63 questions about specific features, both wordings, search only | right one in the top 30 (what the judge reads): **62%** tuning / **65%** held-out; top 5: 50% / 38%. One profile per repo from the same code: 61% / 62%. The finer splits made in testing (85 components): 69% / 77% | same |
| Fit judge, 30 duplicate tickets + 26 "nothing fits" controls, 109 assets | every findable duplicate caught; **0/26** false alarms | `experiments/scale/RESULTS_judge.md` |
| Same at ~9,600 assets | every findable duplicate caught; 3/26 false alarms | same |
| Meaning (vector) search latency, 300k vectors, under row-level security; keyword search was added later and is not yet timed at this size | **~0.1 s** (index), 1.5 s (exact scan) | `experiments/scale/search_load_100k.txt` |
| Freshness, one hour of all public GitHub pushes replayed (111k pushes to main) | change to processed: p95 **225 s**, max 509 s | `experiments/sync/RESULTS.md` |
| Docs pages worth keeping (43 hand-labelled pages) | LLM: precision 0.60, recall 0.92; keyword rule: 0.26 / 0.46 | `experiments/pages/RESULTS.md` |

## Known limits

- **The 85–92% uses test profiles.** Its component profiles (capabilities, search terms,
  example questions) were written by AI models during testing, not by the product.
- **The code reader splits too coarsely.** On 12 public repos it kept 4 repos whole and
  made 60 components where testing made 85, and recall was lower (above). It has not
  been run on a real company's code.
- **Short real questions are harder.** Stack Overflow titles score 81% against 85–92% on
  the test questions; search, not the judge, loses most of them.
- **Recall drops as the org grows.** Look-alike components from other teams are the main
  cause. The current search has not been measured beyond 5,000 assets.
- **LLM cost.** Each question makes two model calls (rewrite and judge). Only a repeated
  question's rewrite is cached.
- **Evidence quality varies by language.** Test names are the main evidence. JavaScript
  `it("rejects a payload over 1 MB")` says a lot; Python `test_max_age` says less.
- **Not production-hardened.** There is no SSO, no syncing of real repo permissions,
  and no real users yet. See [production.md](production.md).
