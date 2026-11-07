"""Does exact vector scan hold at REAL embedding dimensions, under RLS?

This closes an open design question. The earlier measurement
(experiments/RESULTS.md run B) found exact scan at ~76 ms p50 on 500K rows -- but with
64-DIMENSION synthetic vectors. Real embeddings are 384-1024+, which is 6-16x more
arithmetic per row on a scan that has no index to help it. If exact scan does not hold,
the documented fallback is pgvector 0.8 iterative scans, NOT a plain HNSW index, which
silently returned nothing for 56 of 100 users.

Usage: python -I scripts/bench_vector.py [rows] [dims...]
  e.g. python -I scripts/bench_vector.py 50000 384 768 1024

Writes to a throwaway database so it cannot touch the real corpus.
"""

from __future__ import annotations

import statistics as st
import sys
import time

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from homegrown.config import settings

BENCH_DB = "homegrown_bench"
GROUPS, USERS, PER_USER = 200, 60, 3   # ~1.5% of rows visible per user, as in run B


def setup(dsn_admin: str) -> None:
    with psycopg.connect(dsn_admin, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"drop database if exists {BENCH_DB}")
        cur.execute(f"create database {BENCH_DB}")
    # The extension must exist before register_vector() can learn the type OID,
    # so it cannot wait until the first bench_dim connection.
    dsn = dsn_admin.rsplit("/", 1)[0] + f"/{BENCH_DB}"
    with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
        cur.execute("create extension if not exists vector")


def bench_dim(base_dsn: str, rows: int, dim: int, rng) -> dict:
    dsn = base_dsn.rsplit("/", 1)[0] + f"/{BENCH_DB}"
    with psycopg.connect(dsn, autocommit=True) as c:
        register_vector(c)
        with c.cursor() as cur:
            cur.execute("drop table if exists b_asset, b_member cascade")
            cur.execute("create extension if not exists vector")
            cur.execute(f"""
                create table b_asset (
                  id int primary key, scope int not null, vec vector({dim}) not null)""")
            cur.execute("create table b_member (user_id int, group_id int)")

            # Clustered vectors, as real embeddings are clustered by topic.
            centers = rng.normal(size=(60, dim)).astype("float32")
            scopes = rng.integers(0, GROUPS, rows)
            with cur.copy("copy b_asset (id, scope, vec) from stdin") as cp:
                for i in range(rows):
                    v = centers[i % 60] + 0.6 * rng.normal(size=dim).astype("float32")
                    v /= np.linalg.norm(v)
                    cp.write_row((i, int(scopes[i]), v.astype("float32")))
            mem = {}
            with cur.copy("copy b_member (user_id, group_id) from stdin") as cp:
                for u in range(USERS):
                    gs = rng.choice(GROUPS, PER_USER, replace=False).tolist()
                    mem[u] = set(gs)
                    for g in gs:
                        cp.write_row((u, int(g)))
            cur.execute("create index on b_member (user_id)")
            cur.execute("create index on b_asset (scope)")
            cur.execute("analyze b_asset; analyze b_member")

        # Visible-row filter written explicitly here. The real system gets the same
        # restriction from an RLS policy; the point of this bench is the cost of
        # scanning a narrow visible subset, which is identical either way.
        vis = ("scope in (select group_id from b_member where user_id = %s)")
        exact, approx, recalls, zero = [], [], [], 0

        with c.cursor() as cur:
            for u in range(USERS):
                q = rng.normal(size=dim).astype("float32")
                q /= np.linalg.norm(q)

                cur.execute("begin")
                cur.execute("set local enable_indexscan = off")
                cur.execute("set local enable_bitmapscan = off")
                t = time.perf_counter()
                cur.execute(f"select id from b_asset where {vis} "
                            "order by vec <=> %s limit 10", (u, q))
                got = [r[0] for r in cur.fetchall()]
                exact.append(time.perf_counter() - t)
                cur.execute("commit")
                if not got:
                    zero += 1
                truth = set(got)

                cur.execute("begin")
                t = time.perf_counter()
                cur.execute(f"select id from b_asset where {vis} "
                            "order by vec <=> %s limit 10", (u, q))
                got2 = [r[0] for r in cur.fetchall()]
                approx.append(time.perf_counter() - t)
                cur.execute("commit")
                if truth:
                    recalls.append(len(truth & set(got2)) / len(truth))

        # Now with an HNSW index present, to show what the design rejects.
        with c.cursor() as cur:
            cur.execute("set maintenance_work_mem = '512MB'")
            cur.execute("create index b_hnsw on b_asset using hnsw "
                        "(vec vector_cosine_ops) with (m=16, ef_construction=64)")
            cur.execute("analyze b_asset")
        hnsw_lat, hnsw_rec, hnsw_zero, hnsw_ret = [], [], 0, []
        with c.cursor() as cur:
            for u in range(USERS):
                q = rng.normal(size=dim).astype("float32")
                q /= np.linalg.norm(q)
                cur.execute("begin")
                cur.execute("set local enable_indexscan = off")
                cur.execute("set local enable_bitmapscan = off")
                cur.execute(f"select id from b_asset where {vis} "
                            "order by vec <=> %s limit 10", (u, q))
                truth = {r[0] for r in cur.fetchall()}
                cur.execute("commit")

                cur.execute("begin")
                t = time.perf_counter()
                cur.execute(f"select id from b_asset where {vis} "
                            "order by vec <=> %s limit 10", (u, q))
                got = [r[0] for r in cur.fetchall()]
                hnsw_lat.append(time.perf_counter() - t)
                cur.execute("commit")
                hnsw_ret.append(len(got))
                if not got:
                    hnsw_zero += 1
                if truth:
                    hnsw_rec.append(len(truth & set(got)) / len(truth))

    def p(xs, q):
        xs = sorted(xs)
        return 1000 * xs[min(len(xs) - 1, int(q * len(xs)))]

    return {
        "dim": dim, "rows": rows,
        "exact_p50": p(exact, 0.5), "exact_p95": p(exact, 0.95),
        "exact_zero": zero,
        "hnsw_p50": p(hnsw_lat, 0.5),
        "hnsw_recall": st.mean(hnsw_rec) if hnsw_rec else float("nan"),
        "hnsw_returned": st.mean(hnsw_ret) if hnsw_ret else 0,
        "hnsw_zero": hnsw_zero,
    }


def main() -> int:
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 50_000
    dims = [int(x) for x in sys.argv[2:]] or [384, 768, 1024]
    rng = np.random.default_rng(0)

    print(f"rows={rows:,}  users={USERS}  groups={GROUPS}  "
          f"visible/user~{rows*PER_USER//GROUPS:,} ({100*PER_USER/GROUPS:.1f}%)")
    print("pgvector note: 0.8+ iterative scans are the documented fallback\n")
    setup(settings.admin_dsn)

    # NOTE on the second group of columns: they measure the PLANNER'S CHOICE with an
    # HNSW index available -- not HNSW itself. Verified by EXPLAIN: at 768d and 1024d
    # with a 1.5% visible subset, Postgres prefers a bitmap scan on the permission
    # column plus a top-N sort, which is exact, so recall is 1.00 because the index was
    # never used. At 384d it does pick HNSW, and recall collapses. Forcing HNSW for a
    # true like-for-like comparison is still to do.
    print(f"{'dim':>6} {'exact p50':>11} {'exact p95':>11} {'exact 0-res':>12} "
          f"{'planner p50':>12} {'plan recall':>12} {'plan ret/10':>12} {'plan 0-res':>11}")
    out = []
    for d in dims:
        r = bench_dim(settings.admin_dsn, rows, d, rng)
        out.append(r)
        print(f"{r['dim']:>6} {r['exact_p50']:>10.1f}m {r['exact_p95']:>10.1f}m "
              f"{r['exact_zero']:>12} {r['hnsw_p50']:>9.1f}m {r['hnsw_recall']:>12.2f} "
              f"{r['hnsw_returned']:>12.1f} {r['hnsw_zero']:>11}")

    base = next((r for r in out if r["dim"] == dims[0]), out[0])
    print(f"\nscaling from {dims[0]}d: " + ", ".join(
        f"{r['dim']}d = {r['exact_p50']/max(base['exact_p50'],1e-9):.2f}x" for r in out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
