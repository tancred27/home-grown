"""Switch the embedding model, including its dimension, and re-embed every asset.

Vectors are derived data: they can always be rebuilt from assets and claims, so this
drops rows for other models rather than keeping a mixed-dimension column (pgvector
cannot compare vectors of different dimensions, and the search path filters by model).

Usage: python -I scripts/switch_embed_model.py <model-name> <dim>
Then set ORGMEM_EMBED_MODEL / ORGMEM_EMBED_DIM (or config.py defaults) to match.
"""

from __future__ import annotations

import sys

from orgmem.db import admin_conn
from orgmem.embed import asset_text, embed_texts
from orgmem.store import write_embedding


def short(name: str) -> str:
    return name.rstrip("/").rsplit("/", 1)[-1]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    model, dim = sys.argv[1], int(sys.argv[2])
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from embedding where model <> %s", (model,))
            cur.execute(f"alter table embedding alter column vec type vector({dim})")
            cur.execute("""
                select a.id, a.name, coalesce(a.summary,''), a.aliases,
                       coalesce(array_agg(c.body order by c.layer, c.id)
                                filter (where c.id is not null), '{}')
                  from asset a left join claim c on c.asset_id=a.id and c.valid_to is null
                 group by a.id""")
            rows = cur.fetchall()
        card = [asset_text(n, s, list(al or []), list(b or [])) for _, n, s, al, b in rows]
        summ = [f"{short(n)}: {s}" if s else short(n) for _, n, s, _, _ in rows]
        for field, texts in (("card", card), ("summary", summ)):
            vecs = embed_texts(texts, model)
            assert vecs.shape[1] == dim, f"{model} produced {vecs.shape[1]}d, not {dim}d"
            for (aid, *_), v in zip(rows, vecs, strict=True):
                write_embedding(conn, aid, field, model, v)
    print(f"{len(rows)} assets x 2 fields re-embedded with {model} ({dim}d)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
