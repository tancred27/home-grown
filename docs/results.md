# Results

Every number here comes from a script in `scripts/` and a write-up in `experiments/`.
Where it mattered, choices were made on a tuning set and confirmed once on a held-out
test set.

| What | Result | Source |
|---|---|---|
| Finding the right component (51 held-out questions, 109 assets) | right one in the top 5: **94%** | `experiments/scale/RESULTS.md` |
| Same, with ~9,600 assets | **69%** (questions), **80%** (30 duplicate tickets) | same |
| Query rewriting at ~1,900 assets | top-5 recall 69% → **82%** | same |
| Fit judge, 30 duplicate tickets + 26 "nothing fits" controls, 109 assets | every findable duplicate caught; **0/26** false alarms | `experiments/scale/RESULTS_judge.md` |
| Same at ~9,600 assets | every findable duplicate caught; 3/26 false alarms | same |
| Search latency, 300k vectors, under row-level security | **~0.1 s** (index), 1.5 s (exact scan) | `experiments/scale/search_load_100k.txt` |
| Freshness, one hour of all public GitHub pushes replayed (111k pushes to main) | change to processed: p95 **225 s**, max 509 s | `experiments/sync/RESULTS.md` |
| Docs pages worth keeping (43 hand-labelled pages) | LLM: precision 0.60, recall 0.92; keyword rule: 0.26 / 0.46 | `experiments/pages/RESULTS.md` |

## Known limits

- **Recall drops as the org grows**, from about 94% to 69–80% top-5 at ~9,600 assets.
  Look-alike components from other teams are the main cause.
- **LLM cost.** Each question makes two model calls (rewrite and judge), and nothing is
  cached across questions yet.
- **Evidence quality varies by language.** Test names are the main evidence. JavaScript
  `it("rejects a payload over 1 MB")` says a lot; Python `test_max_age` says less.
- **Not production-hardened.** There is no SSO, no syncing of real repo permissions,
  and no real users yet. See [production.md](production.md).
