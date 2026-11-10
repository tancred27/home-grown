"""Latency of bm25_hits() (db/009) at scale, in one rolled-back transaction.

Creates N synthetic assets visible to one test user, each with a summary of `words` words and
`claims` claims of 12 words, drawn from a Zipf-like vocabulary, then times the keyword search
for several queries. Nothing is kept.

    python -I scripts/bm25_load.py [N] [words] [claims]
"""
from __future__ import annotations

import random
import statistics
import sys
import time
import uuid

from homegrown.db import admin_conn

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
WORDS = int(sys.argv[2]) if len(sys.argv) > 2 else 60
CLAIMS = int(sys.argv[3]) if len(sys.argv) > 3 else 5
rng = random.Random(9)
VOCAB = [f"w{i}" for i in range(30000)]
WEIGHTS = [1 / (i + 1) for i in range(len(VOCAB))]
QUERIES = ["w3 w250 w9000", "w12 w4000", "w1 w2 w3 w4 w5", "w20000 w25000", "w77 w777 w7777"]


def text(k: int) -> str:
    return " ".join(rng.choices(VOCAB, WEIGHTS, k=k))


def main() -> None:
    with admin_conn() as conn:
        conn.autocommit = False
        tag = uuid.uuid4().hex[:8]
        team = conn.execute("insert into team (name) values (%s) returning id",
                            (f"team-bmload-{tag}",)).fetchone()[0]
        user = conn.execute("insert into app_user (login, team_id) values (%s,%s) returning id",
                            (f"bmload-{tag}", team)).fetchone()[0]
        conn.execute("insert into membership (user_id, group_id) values (%s,%s)", (user, team))
        ev = conn.execute(
            "insert into evidence (source, ref, source_version, content_hash, extractor_version) "
            "values ('github_code', 'load', 'v', 'h', 't') returning id").fetchone()[0]
        t0 = time.time()
        for start in range(0, N, 2000):
            rows = [(f"bm-{tag}-{i}", f"github:bm-{tag}-{i}", f"github:bm-{tag}-{i}:.", text(WORDS),
                     [text(12) for _ in range(CLAIMS)]) for i in range(start, min(N, start + 2000))]
            with conn.cursor() as cur:
                cur.executemany(
                    "with a as (insert into asset (kind, name, source_ref, unit_id, owner_team, "
                    f"discovery_scope, summary) values ('library', %s, %s, %s, '{team}', '{team}', "
                    "%s) returning id) "
                    "insert into claim (asset_id, layer, kind, body, evidence_id, extractor, "
                    f"confidence) select a.id, 1, 'capability', b, '{ev}', 'load', 0.6 "
                    "from a, unnest(%s::text[]) b", rows)
        print(f"loaded {N} assets ({WORDS} words, {CLAIMS} claims) in {time.time() - t0:.0f}s",
              flush=True)
        conn.execute("analyze asset")
        conn.execute("analyze claim")
        conn.execute("set local role homegrown_app")
        conn.execute("select set_config('app.user_id', %s, true)", (str(user),))
        for q in QUERIES:
            tsq = " | ".join(q.split())
            times = []
            for _ in range(3):
                t = time.perf_counter()
                n = len(conn.execute(
                    "select * from bm25_hits(to_tsquery('english', %s), "
                    "tsvector_to_array(to_tsvector('english', %s)), 200)",
                    (tsq, q)).fetchall())
                times.append(time.perf_counter() - t)
            print(f"{q:22} hits {n:3}  median {statistics.median(times) * 1000:7.1f} ms  "
                  f"max {max(times) * 1000:7.1f} ms", flush=True)
        conn.rollback()


if __name__ == "__main__":
    main()
