"""Which embedding model? Compared on the TUNING set only (dev + held-out).

Semantic retrieval carries almost all of the low-overlap recall, so embedding quality is
the largest remaining lever. Runs in memory (numpy) rather than in Postgres so models of
different dimension can be compared without migrating the vector(768) column first;
the corpus is 86 assets, so an exact in-memory scan is identical to the DB's exact scan.

Each model is used with its own documented query/document prefixes -- comparing a model
without its prefix is comparing a broken configuration.

Retrieval mirrors variant H: max similarity over the "card" and "summary" texts, then
interleaved with the lexical ranking, semantic first.

Usage: python -I scripts/gate1_models.py [model ...]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from gate1_retrieval import auditor, fts_only, indexed_text, toks  # noqa: E402
from gate1_variants import interleave, rank  # noqa: E402

from orgmem.db import admin_conn, as_user  # noqa: E402
from orgmem.embed import asset_text  # noqa: E402

ROOT = Path(__file__).parent.parent / "experiments" / "gate1"

BGE_Q = "Represent this sentence for searching relevant passages: "
MODELS = {
    # name: (query prefix, document prefix)
    "BAAI/bge-base-en-v1.5": (BGE_Q, ""),
    "BAAI/bge-large-en-v1.5": (BGE_Q, ""),
    "mixedbread-ai/mxbai-embed-large-v1": (BGE_Q, ""),
    "snowflake/snowflake-arctic-embed-l": (BGE_Q, ""),
    "thenlper/gte-large": ("", ""),
    "nomic-ai/nomic-embed-text-v1.5": ("search_query: ", "search_document: "),
}


def short(name: str) -> str:
    return name.rstrip("/").rsplit("/", 1)[-1]


def enc(model, texts):
    v = np.array(list(model.embed(texts, batch_size=16)), dtype="float32")
    return v / np.clip(np.linalg.norm(v, axis=1, keepdims=True), 1e-9, None)


def main() -> int:
    from fastembed import TextEmbedding

    wanted = sys.argv[1:] or list(MODELS)
    qs = []
    for f in ("queries.json", "queries_heldout.json"):
        qs += json.loads((ROOT / f).read_text(encoding="utf-8"))

    u = auditor()
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            select a.id, a.unit_id, a.name, coalesce(a.summary,''), a.aliases,
                   coalesce(array_agg(c.body order by c.layer, c.id)
                            filter (where c.id is not null), '{}')
              from asset a left join claim c on c.asset_id=a.id and c.valid_to is null
             group by a.id""")
        assets = cur.fetchall()
    ids = [a[0] for a in assets]
    by_unit = {a[1]: a[0] for a in assets}
    card = [asset_text(a[2], a[3], list(a[4] or []), list(a[5] or [])) for a in assets]
    summ = [f"{short(a[2])}: {a[3]}" if a[3] else short(a[2]) for a in assets]
    qs = [{**q, "gold": by_unit[q["gold_unit_id"]]} for q in qs if q["gold_unit_id"] in by_unit]

    lex, low = [], []
    with as_user(u) as conn:
        for i, q in enumerate(qs):
            lex.append([r["asset_id"] for r in fts_only(conn, q["query"], 30)])
            o = len(toks(q["query"]) & toks(indexed_text(conn, q["gold"]))) / \
                max(1, len(toks(q["query"])))
            if o < 0.20:
                low.append(i)

    def metrics(ranks):
        n = len(ranks)
        r = lambda k, idx=range(n): sum(1 for i in idx if ranks[i] and ranks[i] <= k) / \
            max(1, len(list(idx)))
        return (r(1), r(5), r(10), sum(1 / x if x else 0 for x in ranks) / n, r(5, low))

    print(f"TUNING SET: {len(qs)} queries, {len(low)} low-overlap, {len(ids)} assets\n")
    print(f"{'model':38s} {'':6s} {'r@1':>5} {'r@5':>5} {'r@10':>5} {'MRR':>5} "
          f"{'low@5':>6} {'secs':>5}")
    for name in wanted:
        qp, dp = MODELS.get(name, ("", ""))
        t = time.time()
        try:
            from orgmem.device import providers
            m = TextEmbedding(model_name=name, providers=providers())
            dc = enc(m, [dp + x for x in card])
            ds = enc(m, [dp + x for x in summ])
            qv = enc(m, [qp + q["query"] for q in qs])
        except Exception as e:
            print(f"{name:38s} FAILED {type(e).__name__}: {str(e)[:60]}")
            continue
        secs = time.time() - t
        sim = np.maximum(qv @ dc.T, qv @ ds.T)            # max over fields
        vec_r, hyb_r = [], []
        for i, q in enumerate(qs):
            order = [ids[j] for j in np.argsort(-sim[i])[:30]]
            vec_r.append(rank(order, q["gold"]))
            hyb_r.append(rank(interleave(order, lex[i]), q["gold"]))
        for label, rr in (("vec", vec_r), ("hybrid", hyb_r)):
            a, b, c, d, e = metrics(rr)
            print(f"{name if label == 'vec' else '':38s} {label:6s} {a:5.2f} {b:5.2f} "
                  f"{c:5.2f} {d:5.2f} {e:6.2f} {secs if label == 'vec' else 0:5.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
