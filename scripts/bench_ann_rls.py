"""Can search scale to millions of assets under row-level security?

The product's rule today is "exact scan over the rows the caller can see". That is
correct but linear: measured ~550 ms at 300k visible vectors. This benchmark tests the
replacement: an HNSW index scanned with pgvector 0.8's ITERATIVE index scan, under a real
RLS policy, as the non-superuser app role.

Why it can work here when it failed before: the old failure was a selective filter (a
user sees ~1.5% of rows), so the index's nearest neighbours were mostly filtered away and
users got nothing. In this product most cards are discoverable org-wide, so the filter
removes few rows. And iterative scan keeps walking the index until enough rows pass.

Data: N synthetic 768-d vectors, CLUSTERED (2,000 centres + noise) to look like real
embeddings rather than uniform noise. Stored as halfvec (16-bit) to halve memory.
Visibility: `public_share` of rows are org-wide (scope 0); the rest belong to one of 500
teams. Each query runs as a user in one team, so it sees all org-wide rows plus its
team's rows.

Measured per configuration: p50/p95 latency, recall@10 against the exact answer for the
same user, and how many queries returned fewer than 10 rows (the silent failure).

Everything lives in table zz_ann (dropped at the end unless --keep).

Usage: python -I scripts/bench_ann_rls.py <N> [--public 0.95] [--queries 50] [--keep]
"""

from __future__ import annotations

import sys
import time

import numpy as np
import psycopg
from pgvector import HalfVector
from pgvector.psycopg import register_vector

from orgmem.config import settings

DIM, CENTRES, TEAMS, K = 768, 2000, 500, 10


def arg(name, default, cast=float):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def main() -> int:
    n = int(sys.argv[1])
    public = arg("--public", 0.95)
    nq = arg("--queries", 50, int)
    rng = np.random.default_rng(42)
    centres = rng.standard_normal((CENTRES, DIM)).astype(np.float32)

    def batch(m):
        c = rng.integers(0, CENTRES, m)
        v = centres[c] + 0.6 * rng.standard_normal((m, DIM)).astype(np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    with psycopg.connect(settings.admin_dsn, autocommit=True) as a:
        register_vector(a)
        a.execute("drop table if exists zz_ann")
        a.execute("create table zz_ann (id int primary key, scope int not null, "
                  "vec halfvec(768) not null)")
        t0, done = time.perf_counter(), 0
        while done < n:
            m = min(50_000, n - done)
            v = batch(m)
            scope = np.where(rng.random(m) < public, 0, rng.integers(1, TEAMS + 1, m))
            with a.cursor().copy("copy zz_ann (id, scope, vec) from stdin with (format binary)") as cp:
                cp.set_types(["int4", "int4", "halfvec"])
                for i in range(m):
                    cp.write_row((done + i, int(scope[i]), HalfVector(v[i])))
            done += m
        print(f"loaded {n:,} rows ({public:.0%} org-wide) in {time.perf_counter() - t0:.0f}s",
              flush=True)
        a.execute("alter table zz_ann enable row level security")
        a.execute("create policy zz_ann_read on zz_ann for select to orgmem_app using "
                  "(scope = 0 or scope = nullif(current_setting('app.team', true), '')::int)")
        a.execute("grant select on zz_ann to orgmem_app")
        a.execute("analyze zz_ann")

    queries = batch(nq)
    teams = rng.integers(1, TEAMS + 1, nq)

    def run(label, setup_sql, sql=None):
        sql = sql or "select id from zz_ann order by vec <=> %s limit %s"
        lat, results = [], []
        with psycopg.connect(settings.app_dsn) as c:      # orgmem_app: RLS applies
            register_vector(c)
            for q, t in zip(queries, teams):
                with c.transaction():
                    c.execute("select set_config('app.team', %s, true)", (str(t),))
                    for s in setup_sql:
                        c.execute(s)
                    t0 = time.perf_counter()
                    params = ({"q": HalfVector(q), "k": K} if "%(q)s" in sql
                              else (HalfVector(q), K))
                    rows = c.execute(sql, params).fetchall()
                    lat.append((time.perf_counter() - t0) * 1000)
                    results.append([r[0] for r in rows])
        return lat, results

    exact_lat, exact = run("exact", ["set local enable_indexscan = off"])

    def report(label, lat, res):
        rec = np.mean([len(set(r) & set(e)) / K for r, e in zip(res, exact)])
        short = sum(1 for r in res if len(r) < K)
        print(f"{label:38s} p50 {np.percentile(lat, 50):7.1f} ms  p95 {np.percentile(lat, 95):7.1f} ms"
              f"  recall@10 {rec:.3f}  short results {short}/{nq}", flush=True)

    report("exact scan (today's rule)", exact_lat, exact)

    with psycopg.connect(settings.admin_dsn, autocommit=True) as a:
        t0 = time.perf_counter()
        a.execute("set maintenance_work_mem = '700MB'")
        a.execute("set max_parallel_maintenance_workers = 8")
        a.execute("create index zz_ann_hnsw on zz_ann using hnsw (vec halfvec_cosine_ops) "
                  "with (m = 16, ef_construction = 64)")
        print(f"HNSW index built in {time.perf_counter() - t0:.0f}s", flush=True)

    for ef in (40, 100, 200):
        lat, res = run("hnsw", [f"set local hnsw.ef_search = {ef}",
                                "set local hnsw.iterative_scan = off"])
        report(f"HNSW ef={ef}, no iterative scan", lat, res)
        lat, res = run("hnsw-it", [f"set local hnsw.ef_search = {ef}",
                                   "set local hnsw.iterative_scan = relaxed_order"])
        report(f"HNSW ef={ef}, iterative scan", lat, res)

    # The production "hnsw" mode: a candidate POOL through the index (iterative scan),
    # then exact ranking of the pool (search.py, settings.hnsw_pool / hnsw_ef_search).
    for pool in (400, 1000):
        pool_sql = (f"select id from (select id, vec <=> %s as d from zz_ann "
                    f"order by vec <=> %s limit {pool}) p order by d limit %s")
        lat, res = run("pool", [f"set local hnsw.ef_search = {pool}",
                                "set local hnsw.iterative_scan = relaxed_order"],
                       sql=pool_sql.replace("%s as d", "%(q)s as d")
                       .replace("vec <=> %s limit", "vec <=> %(q)s limit")
                       .replace("limit %s", "limit %(k)s"))
        report(f"HNSW pool={pool} then exact rank", lat, res)

    if "--keep" not in sys.argv:
        with psycopg.connect(settings.admin_dsn, autocommit=True) as a:
            a.execute("drop table zz_ann")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
