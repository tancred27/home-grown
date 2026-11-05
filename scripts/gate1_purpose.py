"""Run 4b: do LLM-written purpose lines help? Decided on the TUNING set only.

Decision rule, fixed before running: adopt the `purpose` field only if hybrid recall@5
on the tuning set (dev + held-out) improves AND low-overlap recall@5 does not fall.

Step 1 (--embed) stores each purpose line as embedding field "purpose". Step 2 (default)
compares, on the tuning set, the frozen run-3 system (max over card+summary) against the
same system with purpose added to the fields.

Usage: python -I scripts/gate1_purpose.py --embed
       python -I scripts/gate1_purpose.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gate1_retrieval import auditor, fts_only, indexed_text, toks  # noqa: E402
from gate1_variants import interleave, rank  # noqa: E402

from orgmem.config import settings  # noqa: E402
from orgmem.db import admin_conn, as_user  # noqa: E402
from orgmem.embed import embed_query, embed_texts  # noqa: E402
from orgmem.search import _FORCE_EXACT  # noqa: E402
from orgmem.store import write_embedding  # noqa: E402

G = Path(__file__).parent.parent / "experiments" / "gate1"


def embed_purpose() -> None:
    lines = json.loads((G / "purpose_summaries.json").read_text(encoding="utf-8"))
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select unit_id, id, name from asset")
        by_unit = {u: (i, n) for u, i, n in cur.fetchall()}
    rows = [(by_unit[x["unit_id"]][0], by_unit[x["unit_id"]][1].rsplit("/", 1)[-1],
             x["purpose"]) for x in lines if x["unit_id"] in by_unit]
    vecs = embed_texts([f"{n}: {p}" for _, n, p in rows])
    with admin_conn() as conn:
        for (aid, _, _), v in zip(rows, vecs, strict=True):
            write_embedding(conn, aid, "purpose", settings.embed_model, v)
    print(f"purpose field embedded for {len(rows)} of {len(lines)} lines")


def vec(conn, q, fields, limit=30):
    with conn.cursor() as cur:
        cur.execute(_FORCE_EXACT)
        cur.execute("select asset_id from embedding where model = %s and field = any(%s) "
                    "group by asset_id order by min(vec <=> %s) limit %s",
                    (settings.embed_model, fields, q, limit))
        return [r[0] for r in cur.fetchall()]


def main() -> int:
    if "--embed" in sys.argv:
        embed_purpose()
        return 0
    qs = []
    for f in ("queries.json", "queries_heldout.json"):
        qs += json.loads((G / f).read_text(encoding="utf-8"))
    u = auditor()
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select unit_id, id from asset")
        by_unit = dict(cur.fetchall())
    qs = [{**q, "gold": by_unit[q["gold_unit_id"]]} for q in qs if q["gold_unit_id"] in by_unit]

    variants = {
        "frozen: vec(card,summary)":         ["card", "summary"],
        "+purpose: vec(card,summary,purpose)": ["card", "summary", "purpose"],
        "purpose only: vec(purpose)":        ["purpose"],
    }
    res = {k: {"vec": [], "hyb": []} for k in variants}
    low = []
    with as_user(u) as conn:
        for i, q in enumerate(qs):
            if len(toks(q["query"]) & toks(indexed_text(conn, q["gold"]))) / \
                    max(1, len(toks(q["query"]))) < 0.20:
                low.append(i)
            qv = embed_query(q["query"])
            lex = [r["asset_id"] for r in fts_only(conn, q["query"], 30)]
            for k, fields in variants.items():
                v = vec(conn, qv, fields)
                res[k]["vec"].append(rank(v, q["gold"]))
                res[k]["hyb"].append(rank(interleave(v, lex), q["gold"]))

    def r(ranks, k, idx=None):
        idx = list(range(len(ranks))) if idx is None else idx
        return sum(1 for i in idx if ranks[i] and ranks[i] <= k) / max(1, len(idx))

    def mrr(ranks):
        return sum(1 / x if x else 0 for x in ranks) / len(ranks)

    print(f"TUNING SET: {len(qs)} queries, {len(low)} low-overlap\n")
    print(f"{'variant':38s} {'':4s} {'r@1':>5} {'r@5':>5} {'r@10':>5} {'MRR':>5} {'low@5':>6}")
    for k in variants:
        for m in ("vec", "hyb"):
            x = res[k][m]
            print(f"{k if m == 'vec' else '':38s} {m:4s} {r(x,1):5.2f} {r(x,5):5.2f} "
                  f"{r(x,10):5.2f} {mrr(x):5.2f} {r(x,5,low):6.2f}")

    base, cand = res["frozen: vec(card,summary)"]["hyb"], \
        res["+purpose: vec(card,summary,purpose)"]["hyb"]
    adopt = r(cand, 5) > r(base, 5) and r(cand, 5, low) >= r(base, 5, low)
    print(f"\nDECISION (pre-registered rule): hybrid r@5 {r(base,5):.2f} -> {r(cand,5):.2f}, "
          f"low-overlap {r(base,5,low):.2f} -> {r(cand,5,low):.2f}  =>  "
          f"{'ADOPT purpose field' if adopt else 'DO NOT ADOPT'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
