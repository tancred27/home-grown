"""Gate 1: does retrieval find the right thing when the wording differs?

Pass line (docs/design.md section 9): >=80% recall@5.

WHY THIS IS BUILT THE WAY IT IS
-------------------------------
Experiment D (experiments/RESULTS.md) produced a headline number that turned out to be
circular: relevance labels came from the npm search topic each package was returned
under, and npm search is keyword-ranked, so "relevant" silently meant "lexically
matches the topic phrase". Appending those same topic words to the query then lifted
precision@10 from 0.20 to 0.98 -- measuring the label leak, not the method.

Two changes here:

1. Labels are a single GOLD ASSET per query, chosen by a human reading the asset, not
   derived from any ranking function.
2. Lexical overlap between the query and the asset's indexed text is MEASURED, and
   recall is reported stratified by it. Queries are still author-written, so some
   vocabulary leaks in no matter how carefully they are phrased -- but low-overlap
   queries are identified by measurement rather than assumed, and those are the ones
   that actually test semantic retrieval. The low-overlap stratum is the real result.

Searching is done as an auditor who can see every asset, so this measures retrieval
alone and not permissions. tests/test_leakage.py covers permissions.

Usage: python -I scripts/gate1_retrieval.py [path/to/queries.json]
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

from orgmem.db import admin_conn, as_user
from orgmem.embed import embed_query
from orgmem.search import _FORCE_EXACT

STOP = set("""a an the and or but if then than that this these those is are was were be
been being to of in on at by for with from as it its into over under about can could
will would should may might must do does did done have has had i you we they he she
them us me my our your their what which who whom when where why how all any both each
few more most other some such no nor not only own same so too very s t just don now
want need get got make made use used using write writing new""".split())


def toks(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower())
            if len(w) > 2 and w not in STOP}


def auditor() -> uuid.UUID:
    """A user in every team with access to every source: sees the whole corpus."""
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into team (name) values ('team-auditor') "
                    "on conflict (name) do update set name = excluded.name returning id")
        t = cur.fetchone()[0]
        cur.execute("insert into app_user (login, team_id) values ('auditor', %s) "
                    "on conflict (login) do update set team_id = excluded.team_id "
                    "returning id", (t,))
        u = cur.fetchone()[0]
        cur.execute("insert into membership (user_id, group_id) "
                    "select %s, id from team on conflict do nothing", (u,))
        cur.execute("insert into source_acl (user_id, source_ref) "
                    "select %s, distinct_src from "
                    "(select distinct source_ref as distinct_src from asset) s "
                    "on conflict do nothing", (u,))
    return u


def indexed_text(conn, asset_id) -> str:
    with conn.cursor() as cur:
        cur.execute("select name, coalesce(summary,''), array_to_string(aliases,' ') "
                    "from asset where id = %s", (asset_id,))
        r = cur.fetchone()
        cur.execute("select coalesce(string_agg(body,' '),'') from claim "
                    "where asset_id = %s and valid_to is null", (asset_id,))
        bodies = cur.fetchone()[0]
    return " ".join([*r, bodies])


def rank_of(rows: list, gold: str) -> int | None:
    for i, r in enumerate(rows):
        if str(r["asset_id"]) == str(gold):
            return i + 1
    return None


def fts_only(conn, terms: str, limit: int) -> list[dict]:
    """OR'd tsquery, same as the product path. See _tsquery_or for why not plainto_."""
    from orgmem.search import _tsquery_or

    tsq = _tsquery_or(terms)
    if not tsq:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """select a.id,
                      max(ts_rank(a.tsv, to_tsquery('english',%s)))
                      + 0.5 * max(coalesce(ts_rank(to_tsvector('english',c.body),
                                    to_tsquery('english',%s)),0)) as s
                 from asset a
                 left join claim c on c.asset_id=a.id and c.valid_to is null
                where a.tsv @@ to_tsquery('english',%s)
                   or to_tsvector('english',coalesce(c.body,'')) @@ to_tsquery('english',%s)
                group by a.id order by s desc limit %s""",
            (tsq, tsq, tsq, tsq, limit))
        return [{"asset_id": r[0]} for r in cur.fetchall()]


def vec_only(conn, terms: str, limit: int) -> list[dict]:
    q = embed_query(terms)
    with conn.cursor() as cur:
        cur.execute(_FORCE_EXACT)
        cur.execute("select a.id from embedding e join asset a on a.id=e.asset_id "
                    "order by e.vec <=> %s limit %s", (q, limit))
        return [{"asset_id": r[0]} for r in cur.fetchall()]


def main() -> int:
    qfile = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).parent.parent / "experiments" / "gate1" / "queries.json"
    if not qfile.is_file():
        print(f"no query set at {qfile}")
        return 2
    qs = json.loads(qfile.read_text(encoding="utf-8"))

    from orgmem.search import search

    u = auditor()
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from asset")
        total = cur.fetchone()[0]
        cur.execute("select unit_id, id from asset")
        by_unit = {r[0]: r[1] for r in cur.fetchall()}

    resolved, missing = [], []
    for q in qs:
        aid = by_unit.get(q["gold_unit_id"])
        (resolved if aid else missing).append({**q, "gold": aid})
    if missing:
        print(f"WARNING: {len(missing)} queries reference assets not in the corpus:")
        for m in missing[:8]:
            print(f"  {m['gold_unit_id']}")

    print(f"corpus: {total} assets | queries: {len(resolved)}\n")

    rows = []
    with as_user(u) as conn:
        for q in resolved:
            text = indexed_text(conn, q["gold"])
            qt, at = toks(q["query"]), toks(text)
            overlap = len(qt & at) / max(1, len(qt))
            r = {
                "query": q["query"], "gold": q["gold"], "overlap": overlap,
                "fts": rank_of(fts_only(conn, q["query"], 20), q["gold"]),
                "vec": rank_of(vec_only(conn, q["query"], 20), q["gold"]),
                "hyb": rank_of(search(conn, q["query"], limit=20)[:20], q["gold"]),
            }
            rows.append(r)

    def recall(rs, key, k):
        hit = sum(1 for r in rs if r[key] and r[key] <= k)
        return hit / max(1, len(rs))

    def mrr(rs, key):
        return sum(1.0 / r[key] if r[key] else 0.0 for r in rs) / max(1, len(rs))

    print(f"{'method':>10} {'recall@1':>9} {'recall@5':>9} {'recall@10':>10} {'MRR':>7} {'misses':>7}")
    for key, label in (("fts", "keyword"), ("vec", "semantic"), ("hyb", "hybrid")):
        print(f"{label:>10} {recall(rows,key,1):>9.2f} {recall(rows,key,5):>9.2f} "
              f"{recall(rows,key,10):>10.2f} {mrr(rows,key):>7.2f} "
              f"{sum(1 for r in rows if not r[key]):>7}")

    # The real result: how each method does when the query does NOT share the asset's
    # vocabulary. Thresholds on measured overlap, not on assumption.
    print("\nby measured query/asset lexical overlap:")
    bands = [("low  <0.20", lambda o: o < 0.20),
             ("mid  0.20-0.45", lambda o: 0.20 <= o < 0.45),
             ("high >=0.45", lambda o: o >= 0.45)]
    print(f"{'band':>16} {'n':>4} {'keyword@5':>10} {'semantic@5':>11} {'hybrid@5':>9}")
    for label, pred in bands:
        sub = [r for r in rows if pred(r["overlap"])]
        if not sub:
            continue
        print(f"{label:>16} {len(sub):>4} {recall(sub,'fts',5):>10.2f} "
              f"{recall(sub,'vec',5):>11.2f} {recall(sub,'hyb',5):>9.2f}")

    print("\nqueries where hybrid missed the gold asset in the top 5:")
    for r in rows:
        if not r["hyb"] or r["hyb"] > 5:
            print(f"  overlap={r['overlap']:.2f} fts={r['fts']} vec={r['vec']} "
                  f"hyb={r['hyb']}  {r['query'][:78]}")

    gate = recall(rows, "hyb", 5)
    print(f"\nGATE 1: hybrid recall@5 = {gate:.2f} "
          f"({'PASS' if gate >= 0.80 else 'FAIL'}, line is 0.80)")
    low = [r for r in rows if r["overlap"] < 0.20]
    if low:
        print(f"  low-overlap stratum (n={len(low)}): hybrid recall@5 = "
              f"{recall(low,'hyb',5):.2f}  <- the honest number")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
