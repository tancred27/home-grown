"""Latency of code_words_hits() (db/011) at scale, in one rolled-back transaction.

Creates N synthetic units visible to one test user, each with code words drawn from a
Zipf-like vocabulary (about `tokens` words a unit, like real crawled units), then times the
search function for several queries. Nothing is kept.

    python -I scripts/code_words_load.py [N] [tokens]
"""
from __future__ import annotations

import random
import statistics
import sys
import time
import uuid

from homegrown.db import admin_conn

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
TOKENS = int(sys.argv[2]) if len(sys.argv) > 2 else 600
rng = random.Random(7)
VOCAB = [f"w{i}" for i in range(50000)]
WEIGHTS = [1 / (i + 1) for i in range(len(VOCAB))]
QUERIES = ["w3 w250 w9000", "w12 w4000", "w1 w2 w3 w4 w5", "w30000 w41000", "w77 w777 w7777"]


def main() -> None:
    with admin_conn() as conn:
        conn.autocommit = False
        tag = uuid.uuid4().hex[:8]
        team = conn.execute("insert into team (name) values (%s) returning id",
                            (f"team-cwload-{tag}",)).fetchone()[0]
        user = conn.execute("insert into app_user (login, team_id) values (%s,%s) returning id",
                            (f"cwload-{tag}", team)).fetchone()[0]
        conn.execute("insert into membership (user_id, group_id) values (%s,%s)", (user, team))
        t0 = time.time()
        batch = 2000
        for start in range(0, N, batch):
            rows = []
            for i in range(start, min(N, start + batch)):
                rows.append((f"cw-{tag}-{i}", f"github:cw-{tag}-{i}", f"github:cw-{tag}-{i}:.",
                             " ".join(rng.choices(VOCAB, WEIGHTS, k=TOKENS))))
            with conn.cursor() as cur:
                cur.executemany(
                    "with a as (insert into asset (kind, name, source_ref, unit_id, owner_team, "
                    "discovery_scope) values ('library', %s, %s, %s, "
                    f"'{team}', '{team}') returning id) "
                    "insert into code_words (asset_id, tsv) select a.id, to_tsvector('simple', %s) "
                    "from a", rows)
        print(f"loaded {N} units x {TOKENS} words in {time.time() - t0:.0f}s", flush=True)
        conn.execute("analyze code_words")
        conn.execute("set local role homegrown_app")
        conn.execute("select set_config('app.user_id', %s, true)", (str(user),))
        for q in QUERIES:
            lex = q.split()
            tsq = " | ".join(lex)
            times = []
            for _ in range(5):
                t = time.perf_counter()
                n = len(conn.execute(
                    "select * from code_words_hits(to_tsquery('simple', %s), %s, 200)",
                    (tsq, lex)).fetchall())
                times.append(time.perf_counter() - t)
            print(f"{q:22} hits {n:3}  median {statistics.median(times) * 1000:7.1f} ms  "
                  f"max {max(times) * 1000:7.1f} ms", flush=True)
        conn.rollback()


if __name__ == "__main__":
    main()
