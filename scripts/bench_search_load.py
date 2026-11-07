"""Search under concurrent load and under churn (tests B and C).

ONLY against orgmem_bench. Loads N synthetic assets (3 clustered 768-d vectors each,
summaries from a word pool so keyword search has work), visible to one bench user,
then runs the PRODUCT's search() as that user under RLS:

  B  latency (p50/p95) and questions/second at 1, 10, 50, 200 concurrent askers,
     with and without a background writer re-embedding assets (a sync worker),
     in exact mode and in hnsw mode
  C  hnsw accuracy vs exact (overlap of the top 10) after replacing 10% and 30% of the
     vectors, before and after VACUUM

LLM time is excluded (it's measured separately: ~1-2 s per call, 2 calls per question).

    python -I scripts/bench_search_load.py [--assets 100000] [--seconds 20]
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from orgmem.config import settings

if "orgmem_bench" not in settings.admin_dsn:
    raise SystemExit("refusing to run: ORGMEM_ADMIN_DSN must point at orgmem_bench")
settings.db_pool_size = 60

from orgmem.db import admin_conn, as_user  # noqa: E402
from orgmem.embed import embed_query  # noqa: E402
from orgmem.search import search  # noqa: E402

ROOT = Path(__file__).parent.parent
DIM, CENTRES = 768, 3000
WORDS = ("retry backoff http client cache metrics exporter prometheus dashboard alert rule "
         "queue worker scheduler storage index search vector auth token session proxy "
         "gateway parser config secret logging tracing span ingest pipeline stream kafka "
         "lambda deploy helm chart operator controller plugin datasource query sql json").split()


def arg(name, default, cast=int):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def setup(n: int, rng) -> str:
    with admin_conn() as conn:
        conn.execute("delete from asset where unit_id like 'bench:%'")
        team = conn.execute("insert into team (name) values ('team-bench') on conflict (name) "
                            "do update set name = excluded.name returning id").fetchone()[0]
        user = conn.execute("insert into app_user (login, team_id) values ('bench-user', %s) "
                            "on conflict (login) do update set team_id = excluded.team_id "
                            "returning id", (team,)).fetchone()[0]
        conn.execute("insert into membership (user_id, group_id) values (%s,%s) "
                     "on conflict do nothing", (user, team))
    centres = rng.standard_normal((CENTRES, DIM)).astype(np.float32)
    t0 = time.perf_counter()
    with psycopg.connect(settings.admin_dsn, autocommit=True) as c:
        register_vector(c)
        ids = [uuid.uuid4() for _ in range(n)]
        with c.cursor().copy("copy asset (id, kind, name, source_ref, unit_id, owner_team, "
                             "discovery_scope, summary) from stdin") as cp:
            for i, aid in enumerate(ids):
                summ = " ".join(random.choices(WORDS, k=8))
                cp.write_row((aid, "library", f"bench-{i}", f"bench:{i}", f"bench:{i}",
                              team, team, summ))
        for k in range(0, n, 20000):
            chunk = ids[k:k + 20000]
            with c.cursor().copy("copy embedding (asset_id, field, model, vec) from stdin "
                                 "with (format binary)") as cp:
                cp.set_types(["uuid", "text", "text", "vector"])
                for aid in chunk:
                    base = centres[rng.integers(CENTRES)]
                    for f in ("card", "summary", "purpose"):
                        v = base + 0.5 * rng.standard_normal(DIM).astype(np.float32)
                        cp.write_row((aid, f, settings.embed_model, v / np.linalg.norm(v)))
        c.execute("analyze asset"); c.execute("analyze embedding")
    print(f"loaded {n:,} assets / {3 * n:,} vectors in {time.perf_counter() - t0:.0f}s",
          flush=True)
    return str(user)


def load_test(user, queries, conc, seconds, writer: bool) -> dict:
    stop = threading.Event()
    lat: list[float] = []
    lock = threading.Lock()
    writes = [0]

    def asker(i):
        k = i
        while not stop.is_set():
            q = queries[k % len(queries)]
            k += conc
            t0 = time.perf_counter()
            with as_user(user) as conn:
                search(conn, q)
            with lock:
                lat.append(time.perf_counter() - t0)

    def sync_writer():
        rng = np.random.default_rng(7)
        while not stop.is_set():
            with admin_conn() as conn:
                ids = [r[0] for r in conn.execute(
                    "select id from asset where unit_id like 'bench:%' "
                    "order by random() limit 20").fetchall()]
                for aid in ids:
                    v = rng.standard_normal(DIM).astype(np.float32)
                    conn.execute("update embedding set vec = %s where asset_id = %s "
                                 "and field = 'card'", (v / np.linalg.norm(v), aid))
                writes[0] += len(ids)
            time.sleep(0.2)

    threads = [threading.Thread(target=asker, args=(i,), daemon=True) for i in range(conc)]
    if writer:
        threads.append(threading.Thread(target=sync_writer, daemon=True))
    for t in threads:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join(timeout=120)
    lat.sort()
    return {"concurrency": conc, "writer": writer, "questions": len(lat),
            "qps": round(len(lat) / seconds, 1),
            "p50_ms": round(1000 * lat[len(lat) // 2]) if lat else None,
            "p95_ms": round(1000 * lat[int(.95 * (len(lat) - 1))]) if lat else None,
            "writes_per_s": round(writes[0] / seconds, 1)}


def topk(user, qvecs, mode):
    out = []
    with as_user(user) as conn:
        for qv in qvecs:
            if mode == "exact":
                conn.execute("set local enable_indexscan = off")
                rows = conn.execute("select asset_id from embedding where model = %s "
                                    "order by vec <=> %s limit 10",
                                    (settings.embed_model, qv)).fetchall()
            else:
                conn.execute("set local enable_indexscan = on")
                conn.execute("set local hnsw.iterative_scan = relaxed_order")
                conn.execute(f"set local hnsw.ef_search = {settings.hnsw_ef_search}")
                rows = conn.execute(
                    "select asset_id from (select asset_id, vec <=> %s d from embedding "
                    "where model = %s order by vec <=> %s limit %s) p order by d limit 10",
                    (qv, settings.embed_model, qv, settings.hnsw_pool)).fetchall()
            out.append([r[0] for r in rows])
    return out


def main() -> int:
    n, seconds = arg("--assets", 100_000), arg("--seconds", 20)
    rng = np.random.default_rng(42)
    random.seed(42)
    user = setup(n, rng)
    qs = [q["query"] for f in ("queries.json", "queries_heldout.json")
          for q in json.loads((ROOT / "experiments/gate1" / f).read_text("utf-8"))]
    report = {"assets": n, "vectors": 3 * n, "B": [], "C": [], "embed": {}}

    # Question embedding (CPU model) measured on its own, then taken out of the DB test:
    # every search() embeds its question first, and under concurrency that, not the
    # database, dominated latency in the smoke run.
    import orgmem.search as S
    t0 = time.perf_counter()
    cache = {q: embed_query(q) for q in qs}
    report["embed"]["sequential_ms_per_question"] = round(1000 * (time.perf_counter() - t0) / len(qs))
    with ThreadPoolExecutor(10) as ex:
        t0 = time.perf_counter()
        list(ex.map(embed_query, qs))
        report["embed"]["questions_per_s_10_threads"] = round(len(qs) / (time.perf_counter() - t0), 1)
    print("embedding:", report["embed"], flush=True)
    S.embed_query = lambda text, model=None: cache[text]

    for mode in ("exact", "hnsw"):
        settings.vector_search = mode
        if mode == "hnsw":
            t0 = time.perf_counter()
            with psycopg.connect(settings.admin_dsn, autocommit=True) as c:
                c.execute("set maintenance_work_mem = '700MB'")
                c.execute("set max_parallel_maintenance_workers = 8")
                c.execute("drop index if exists embedding_vec_hnsw")
                c.execute("create index embedding_vec_hnsw on embedding using hnsw "
                          "(vec vector_cosine_ops) with (m = 16, ef_construction = 64)")
            print(f"hnsw index built in {time.perf_counter() - t0:.0f}s", flush=True)
        for conc in (1, 10, 50, 200):
            for writer in (False, True):
                r = {"mode": mode, **load_test(user, qs, conc, seconds, writer)}
                report["B"].append(r)
                print(json.dumps(r), flush=True)

    # C: churn, hnsw vs exact on the same data
    qvecs = [cache[q] for q in qs[:50]]

    def overlap():
        ex, hn = topk(user, qvecs, "exact"), topk(user, qvecs, "hnsw")
        return round(statistics.mean(len(set(a) & set(b)) / 10 for a, b in zip(ex, hn)), 3)
    report["C"].append({"replaced": 0, "vacuum": False, "overlap@10": overlap()})
    for frac in (0.10, 0.30):
        with admin_conn() as conn:
            ids = [r[0] for r in conn.execute(
                "select id from asset where unit_id like 'bench:%%' order by random() limit %s",
                (int(frac * n),)).fetchall()]
            centres = rng.standard_normal((CENTRES, DIM)).astype(np.float32)
            with conn.cursor() as cur:
                rows = []
                for aid in ids:
                    v = centres[rng.integers(CENTRES)] + 0.5 * rng.standard_normal(DIM).astype(np.float32)
                    rows.append((v / np.linalg.norm(v), aid))
                cur.executemany("update embedding set vec = %s where asset_id = %s "
                                "and field = 'card'", rows)
        report["C"].append({"replaced": frac, "vacuum": False, "overlap@10": overlap()})
        with psycopg.connect(settings.admin_dsn, autocommit=True) as c:
            c.execute("vacuum embedding")
        report["C"].append({"replaced": frac, "vacuum": True, "overlap@10": overlap()})
        print(json.dumps(report["C"][-2:]), flush=True)

    out = ROOT / "experiments" / "scale" / f"search_load_{n}.json"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    with admin_conn() as conn:
        conn.execute("delete from asset where unit_id like 'bench:%'")
    print(f"written {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
