"""Add a clean `summary` embedding field alongside the existing `card` field.

Hypothesis being tested: the `card` vector embeds name + summary + up to 20 claim
bodies, and on most assets those bodies are test names ("escapes backslashes in label
names"). That is the same dilution that broke the reranker's input (see
experiments/gate1/RESULTS.md run 2), so the description may be drowned in the vector too.
The `embedding` table already keys on (asset_id, field, model), so this adds a field
without touching the existing one.

Usage: python -I scripts/add_field_embeddings.py
"""

from __future__ import annotations

from homegrown.config import settings
from homegrown.db import admin_conn
from homegrown.embed import embed_texts
from homegrown.store import write_embedding


def short_name(name: str) -> str:
    """github.com/grafana/xk6-kafka -> xk6-kafka. Module paths are noise to an embedder."""
    return name.rstrip("/").rsplit("/", 1)[-1]


def main() -> int:
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id, name, coalesce(summary, '') from asset")
            rows = cur.fetchall()
        texts = [f"{short_name(n)}: {s}" if s else short_name(n) for _, n, s in rows]
        vecs = embed_texts(texts)
        for (aid, _, _), v in zip(rows, vecs, strict=True):
            write_embedding(conn, aid, "summary", settings.embed_model, v)
    print(f"summary field embedded for {len(rows)} assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
