# State-based sync under real traffic (branch `state-sync`, 2026-11-08)

Question: can the write path keep up when an org lands thousands to millions of commits
a day (coding agents included)? Everything ran against a throwaway copy of the database
(`orgmem_bench`).

## Before: one agent run per commit (`scripts/bench_write_path.py`)

300 synthetic commits on 32 real repos (local checkouts, no network, LLM off):
**0.8 commits/s per worker**. At 1M commits/day that is 15+ workers flat out, before
any GitHub API limit. Profile: most time was spent waiting on the database (~41 new
connections and ~53 round trips per commit), then embedding (~13%). Connection pooling
made intake 10× faster (33 → 332 events/s) but processing stayed ~0.9/s: the cost was
per-run overhead, so the number of runs had to come down.

## After: state-based sync

Design (agreed in discussion): intake only marks a repo dirty; a repo is due when quiet
2 min OR pending 8 min; one worker per repo (lease); a pass reads the default branch's
current state, compares Git fingerprints (tree SHA per unit folder, blob SHA per file
the collector reads) and runs the agent only on units whose readable files changed.
Contributors and stage move to a daily refresh.

### 1. All of public GitHub as one org (`scripts/bench_intake_replay.py`, `intake_replay.txt`)

GH Archive, 2024-10-15 15:00–16:00 UTC: 146,350 pushes (~663,000 commits, i.e. about
16 million a day), of which **111,019 pushes to main/master across 53,211 repos**.
Replayed in order on a simulated clock through the real intake and scheduler SQL.

| | measured | needed | headroom |
|---|---|---|---|
| intake, one dirty-mark per request (webhook style) | 417/s | 31/s | ~13× |
| intake, batched (poller style) | 5,728/s | 31/s | ~185× |
| scheduler claims | 238/s | ~22/s | ~11× |
| staleness (change → processed) | p50 137 s, p95 225 s, p99 419 s, **max 509 s** | < 600 s | ✓ |

78,015 passes for 111,019 pushes (coalescing 1.42: across all of GitHub most repos get
one push an hour; busy repos inside one org coalesce far more, see below).
Note: GH Archive data from late 2026 is heavily thinned (3,442 pushes in a sampled hour),
which is why a 2024 hour was used.

### 2. Real busy repos, full pipeline (`scripts/bench_busy_repos.py`, `busy_repos.json`)

Bare clones (12 days); each repo's busiest 24 h of its default branch replayed with a
pass every 10 minutes at the real head of that moment. LLM off (purpose lines fail
safe); embeddings, extraction and database writes real.

| repo | commits / 24 h | landed on main | passes doing work | s per pass (mean / p95) | **worker s / day** | backfill |
|---|---|---|---|---|---|---|
| kubernetes/kubernetes | 36 | 24 | 7 / 144 | 0.90 / 1.53 | **130** | 40 units, 82 s |
| microsoft/vscode | 154 | 118 | 9 / 144 | 0.36 / 1.43 | **52** | 117 units, 90 s |
| rust-lang/rust | 117 | 7 | 1 / 144 | 0.10 / 0.10 | **14** | 265 units, 86 s |

17 of 432 passes needed the agent at all (1–2 units each); the rest were "head
unchanged" or "only source code changed" (fingerprints of what we read untouched).
Three of GitHub's busiest repos cost ~3 worker-minutes a day combined.

Backfill reading files one at a time over the network ran at ~1 file/s (kubernetes
alone needed ~1,800 reads → ~30 min). From a bulk clone (all three repos downloaded in
under 2 minutes) each backfill took ~1.5 min. **Backfills must use bulk fetches.**

### 3. Real polling cost (`polling_cost.txt`)

One poll = list the org's repos sorted by latest push and stop at the last poll time.
microsoft (~7k repos), apache (~2.5k), NixOS, kubernetes: **1 API call per org per
cycle** for both 10- and 60-minute windows. At a 5-minute interval: 12 calls/hour per
org (limit 5,000/hour).

## What it means

Work scales with **active repos × passes**, not with commits: at most one pass per
repo per ~10 minutes, most of them nearly free. Worst case for a 10,000-repo org whose
every repo changes every 10 minutes: ~1.4M passes/day ≈ 8–10 workers. A typical org:
far fewer.

## Bugs found by real repos (fixed, with tests)

- Windows 260-character path limit: kubernetes' deeply nested generated tests broke the
  materialised checkout. Short working folder; over-long paths skipped and counted.
- `stage.infer` recursed forever when a README contained "deprecated" (kubernetes).

## Not yet measured

- LLM cost under sustained load (purpose lines for changed units, page decisions);
  measured separately in earlier experiments (~1.5k tokens per call).
- Very large single repos beyond the API's tree limit (100k entries): the code refuses
  rather than miss units; local mirrors are the path (implemented in LocalGitSource).
- `nixpkgs`: its shallow clone came back with almost no history, so it wasn't replayed.
