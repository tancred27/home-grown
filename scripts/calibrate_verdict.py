"""Calibrate the verdict's relevance threshold. Uses gate-1 TUNING queries only.

The rule-based verdict ignored relevance: any candidate with test claims became
"use_as_is", so every gate-2 control ticket raised a false alarm. The fix is a minimum
semantic similarity below which a candidate can only be "unknown".

Positives: each tuning query's similarity to its gold asset.
Negatives: leave-one-out -- the best similarity to any asset OTHER than the gold one,
i.e. what the top match looks like when the thing the developer wants does not exist.
Similarity is the product's own: cosine, max over the asset's embedding fields.

Prints the distributions and the threshold that maximises TPR - FPR (Youden's J).
The held-out gate-2 tickets are never touched here.

Usage: python -I scripts/calibrate_verdict.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from gate1_retrieval import auditor  # noqa: E402

from homegrown.config import settings  # noqa: E402
from homegrown.db import admin_conn, as_user  # noqa: E402
from homegrown.embed import embed_query  # noqa: E402

G = Path(__file__).parent.parent / "experiments" / "gate1"


def main() -> int:
    qs = []
    for f in ("queries.json", "queries_heldout.json"):
        qs += json.loads((G / f).read_text(encoding="utf-8"))
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select unit_id, id from asset")
        by_unit = dict(cur.fetchall())
    qs = [q for q in qs if q["gold_unit_id"] in by_unit]
    u = auditor()

    pos, neg = [], []
    with as_user(u) as conn, conn.cursor() as cur:
        for q in qs:
            qv = embed_query(q["query"])
            cur.execute("select asset_id, 1 - min(vec <=> %s) from embedding "
                        "where model = %s group by asset_id", (qv, settings.embed_model))
            sims = {str(a): float(s) for a, s in cur.fetchall()}
            gold = str(by_unit[q["gold_unit_id"]])
            pos.append(sims[gold])
            neg.append(max(s for a, s in sims.items() if a != gold))
    pos, neg = np.array(pos), np.array(neg)

    def q(a):
        return " ".join(f"{np.percentile(a, p):.3f}" for p in (5, 25, 50, 75, 95))

    print(f"tuning queries: {len(qs)}   similarity percentiles (5 25 50 75 95)")
    print(f"  positive (query vs its gold asset):        {q(pos)}")
    print(f"  negative (best match when gold is absent):  {q(neg)}")

    best = None
    print(f"\n{'threshold':>9} {'TPR':>6} {'FPR':>6} {'J':>6}")
    for t in np.arange(0.55, 0.86, 0.01):
        tpr, fpr = float((pos >= t).mean()), float((neg >= t).mean())
        j = tpr - fpr
        if best is None or j > best[3]:
            best = (round(float(t), 2), tpr, fpr, j)
        if abs((t * 100) % 3) < 1e-6 or abs(t - best[0]) < 1e-9:
            print(f"{t:9.2f} {tpr:6.2f} {fpr:6.2f} {j:6.2f}")
    print(f"\nbest by Youden J: threshold={best[0]:.2f}  TPR={best[1]:.2f}  "
          f"FPR={best[2]:.2f}  J={best[3]:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
