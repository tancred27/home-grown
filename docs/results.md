# Results

Every number here comes from a script in `scripts/` and a write-up in `experiments/`.
Where it mattered, choices were made on a tuning set and confirmed once on a held-out
test set.

| What | Result | Source |
|---|---|---|
| Finding the right component, 5,000 assets, 108 test questions | right one in the top 5: **85%**; **92%** with the same questions in casual wording (previous search: 79% / 88%) | evaluation write-up not published yet |
| Fit judge, 30 duplicate tickets + 26 "nothing fits" controls, 109 assets | every findable duplicate caught; **0/26** false alarms | `experiments/scale/RESULTS_judge.md` |
| Same at ~9,600 assets | every findable duplicate caught; 3/26 false alarms | same |
| Search latency, 300k vectors, under row-level security | **~0.1 s** (index), 1.5 s (exact scan) | `experiments/scale/search_load_100k.txt` |
| Freshness, one hour of all public GitHub pushes replayed (111k pushes to main) | change to processed: p95 **225 s**, max 509 s | `experiments/sync/RESULTS.md` |
| Docs pages worth keeping (43 hand-labelled pages) | LLM: precision 0.60, recall 0.92; keyword rule: 0.26 / 0.46 | `experiments/pages/RESULTS.md` |

## Known limits

- **The product's own profiles are not measured yet.** The 85–92% uses component
  profiles (capabilities, search terms, example questions) that AI models wrote during
  testing. The code reader (`HOMEGROWN_CRAWL_ENABLED`) now writes them, but its results
  have not been measured yet.
- **Recall drops as the org grows.** Look-alike components from other teams are the main
  cause. The current search has not been measured beyond 5,000 assets.
- **LLM cost.** Each question makes two model calls (rewrite and judge), and nothing is
  cached across questions yet.
- **Evidence quality varies by language.** Test names are the main evidence. JavaScript
  `it("rejects a payload over 1 MB")` says a lot; Python `test_max_age` says less.
- **Not production-hardened.** There is no SSO, no syncing of real repo permissions,
  and no real users yet. See [production.md](production.md).
