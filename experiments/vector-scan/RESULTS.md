# Does exact vector scan hold at real embedding dimensions?

Run 2026-11-05. Closes an open question from the design notes.
Reproduce: `python -I scripts/bench_vector.py 50000 384 768 1024`

## Why this needed re-measuring

The original result (`experiments/RESULTS.md` run B) found exact scan at ~76 ms p50 on
500K rows — but with **64-dimension synthetic vectors**. Real embeddings are 384–1024+,
which is 6–16x more arithmetic per row on a scan with no index to help it. The worry was
that the design's "always exact scan" rule was only affordable because of an unrealistic
vector width.

Setup: pgvector 0.8.1 on Postgres 16, 50,000 rows, 200 groups, 60 users, 3 groups each —
so ~750 rows (1.5%) visible per user, matching run B's selectivity.

## Result

| dim | exact p50 | exact p95 | zero-result users |
|---|---|---|---|
| 384 | 7.2 ms | 15.7 ms | 0 |
| 768 | 5.9 ms | 9.7 ms | 0 |
| 1024 | 6.7 ms | 8.1 ms | 0 |

**Exact scan holds, and it is essentially flat in dimension** — 0.81x to 1.00x going from
384 to 1024, not the 6–16x I predicted.

### Why it is flat, and the better mental model

`EXPLAIN ANALYZE` shows the planner using a **Bitmap Heap Scan on the permission
column**, then a top-N heapsort on the distance — over 727 rows, not 50,000:

```
Limit
  -> Sort  (Sort Key: vec <=> $0, top-N heapsort, 25kB)
       -> Nested Loop (actual rows=727)
            -> Seq Scan on b_member (Filter: user_id = 0)
            -> Bitmap Heap Scan on b_asset (Recheck Cond: scope = group_id)
```

So the distance work is only ever done over the rows the caller can already see.
**Cost scales with visible rows, not with total rows and not with dimension.** That is a
better governing variable than "500K rows → 76 ms", and it is a happy consequence of the
permission filter being selective: the tighter the silo, the cheaper the search.

### A correction to this benchmark, and to run B's lesson

The second group of columns in the script output is **mislabelled as HNSW**. It measures
*the planner's choice with an HNSW index available*, which is not the same thing:

| dim | planner p50 | recall | returned/10 | zero-result users |
|---|---|---|---|---|
| 384 | 1.5 ms | **0.04** | **0.4** | **39 of 60** |
| 768 | 3.0 ms | 1.00 | 10.0 | 0 |
| 1024 | 3.3 ms | 1.00 | 10.0 | 0 |

The 768 and 1024 rows show perfect recall **because the index was never used** —
verified by EXPLAIN above. At 384 the planner did choose HNSW, and recall collapsed to
0.04 with 39 of 60 users getting nothing, reproducing run B's failure.

So run B's lesson is narrower than "HNSW fails under RLS". It is: **HNSW fails when the
planner chooses it over the filtered path**, and whether it does so depends on index size
relative to the filtered row count — which varies with dimension. At realistic
dimensions Postgres already prefers the strategy the design mandates.

That does not make the rule redundant. `search.py` keeps `enable_indexscan = off` on the
vector statement, because relying on the planner to keep making the right choice as data
grows is a hope, not a guarantee, and the failure mode is silent.

## Still to do

- Force HNSW explicitly for a true like-for-like comparison at each dimension.
- Re-run at 500K rows. 50K was enough to answer the dimension question, but not the
  scale question.
- Test pgvector 0.8 iterative scans as the documented fallback. Note
  `hnsw.iterative_scan` was not a recognised GUC in this session, which needs
  investigating before relying on it.
- These are synthetic clustered vectors, not real embeddings of real cards. The real
  corpus is currently 7 assets, far too small to measure.
