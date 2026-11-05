"""Compare retrieval variants on the TUNING set (dev + held-out). Never the test set.

Each variant is a small, principled change, not a parameter sweep: 106 queries is too
few to sweep over without fitting noise. The winner here is then run once on
experiments/gate1/queries_test.json by scripts/gate1_retrieval.py.

Usage: python -I scripts/gate1_variants.py [--rerank]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# `python -I` does not put the script's own directory on sys.path.
sys.path.insert(0, str(Path(__file__).parent))
from gate1_retrieval import auditor, fts_only, indexed_text, toks  # noqa: E402

from orgmem.config import settings
from orgmem.db import admin_conn, as_user
from orgmem.embed import embed_query
from orgmem.search import _FORCE_EXACT, candidate_text

ROOT = Path(__file__).parent.parent / "experiments" / "gate1"
K = 60.0


def vec(conn, q, field: str | None, limit=30):
    """field=None means max similarity over all fields of an asset."""
    with conn.cursor() as cur:
        cur.execute(_FORCE_EXACT)
        if field:
            cur.execute("select asset_id from embedding where field = %s "
                        "order by vec <=> %s limit %s", (field, q, limit))
        else:
            cur.execute("select asset_id, min(vec <=> %s) d from embedding "
                        "group by asset_id order by d limit %s", (q, limit))
        return [r[0] for r in cur.fetchall()]


def fuse_max(a: list, b: list) -> list:
    s: dict = {}
    for lst in (a, b):
        for i, x in enumerate(lst):
            s[x] = max(s.get(x, 0.0), 1.0 / (K + i + 1))
    both = set(a) & set(b)
    return sorted(s, key=lambda x: (-s[x], -(x in both)))


def fuse_weighted(primary: list, secondary: list, w: float = 0.75) -> list:
    """Weighted RRF: the stronger retriever counts more, agreement still helps."""
    s: dict = {}
    for i, x in enumerate(primary):
        s[x] = s.get(x, 0.0) + w / (K + i + 1)
    for i, x in enumerate(secondary):
        s[x] = s.get(x, 0.0) + (1 - w) / (K + i + 1)
    return sorted(s, key=lambda x: -s[x])


def interleave(primary: list, secondary: list) -> list:
    """Max-fusion with ties broken toward the primary retriever.

    Plain max-fusion gives semantic #1 and lexical #1 the identical score 1/61, so
    which one ends up first is arbitrary -- and lexical #1 is often noise, which is
    why recall@1 collapsed to ~0.33 for every max-fused variant. Breaking the tie
    toward the measured-stronger retriever is the same as interleaving the lists.
    """
    out, seen = [], set()
    for i in range(max(len(primary), len(secondary))):
        for lst in (primary, secondary):
            if i < len(lst) and lst[i] not in seen:
                seen.add(lst[i])
                out.append(lst[i])
    return out


def rank(lst, gold):
    for i, x in enumerate(lst):
        if str(x) == str(gold):
            return i + 1
    return None


def main() -> int:
    do_rerank = "--rerank" in sys.argv
    qs = []
    for f in ("queries.json", "queries_heldout.json"):
        qs += json.loads((ROOT / f).read_text(encoding="utf-8"))

    u = auditor()
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select unit_id, id from asset")
        by_unit = {r[0]: r[1] for r in cur.fetchall()}
    qs = [{**q, "gold": by_unit[q["gold_unit_id"]]} for q in qs if q["gold_unit_id"] in by_unit]

    enc = None
    if do_rerank:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        enc = TextCrossEncoder(model_name="BAAI/bge-reranker-base")

    results: dict[str, list] = {}
    overlaps = []
    with as_user(u) as conn:
        for q in qs:
            overlaps.append(len(toks(q["query"]) & toks(indexed_text(conn, q["gold"])))
                            / max(1, len(toks(q["query"]))))
            qv = embed_query(q["query"])
            f = [r["asset_id"] for r in fts_only(conn, q["query"], 30)]
            vc, vs, vm = vec(conn, qv, "card"), vec(conn, qv, "summary"), vec(conn, qv, None)
            variants = {
                "A  vec card (current semantic)": vc,
                "B  vec summary": vs,
                "C  vec max(card,summary)": vm,
                "D  max-fuse fts+card (current hybrid)": fuse_max(f, vc),
                "E  max-fuse fts+max": fuse_max(f, vm),
                "F  weighted fts+max (w=.75)": fuse_weighted(vm, f),
                "H  interleave max-vec then fts": interleave(vm, f),
            }
            if enc:
                head = variants["H  interleave max-vec then fts"][:20]
                texts = [candidate_text(conn, a) for a in head]
                sc = list(enc.rerank(q["query"], texts))
                variants["G  H + bge-reranker-base top20"] = [
                    a for _, a in sorted(zip(sc, head), key=lambda t: -t[0])]
            for name, lst in variants.items():
                results.setdefault(name, []).append(rank(lst, q["gold"]))

    n = len(qs)
    low = [i for i, o in enumerate(overlaps) if o < 0.20]

    def r_at(ranks, k, idx=None):
        idx = range(len(ranks)) if idx is None else idx
        idx = list(idx)
        return sum(1 for i in idx if ranks[i] and ranks[i] <= k) / max(1, len(idx))

    def mrr(ranks):
        return sum(1 / r if r else 0 for r in ranks) / len(ranks)

    print(f"TUNING SET: {n} queries (dev + held-out), {len(low)} low-overlap\n")
    print(f"{'variant':42s} {'r@1':>5} {'r@5':>5} {'r@10':>5} {'MRR':>5} {'low r@5':>8}")
    for name, ranks in results.items():
        print(f"{name:42s} {r_at(ranks,1):5.2f} {r_at(ranks,5):5.2f} "
              f"{r_at(ranks,10):5.2f} {mrr(ranks):5.2f} {r_at(ranks,5,low):8.2f}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
