"""Cross-encoder reranking.

Why a reranker and not more fusion tuning: the retrievers score a query against a
document independently, so neither ever sees the pair together. A cross-encoder reads
both at once, which is what lets it tell "detect the moment where behaviour shifts"
(changepoint) from "spot unusual points" (outlier) -- two assets whose embeddings sit
almost on top of each other. Gate 1's recall@10 was 0.86 against recall@5 of 0.76, so
the right answer was usually already in the candidate set and just ranked too low.
That gap is exactly what reranking is for.

Runs locally on onnxruntime, ~80MB, no torch.
"""

from __future__ import annotations

import functools

from .config import settings


@functools.lru_cache(maxsize=2)
def _encoder(name: str):
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    from .device import providers

    return TextCrossEncoder(model_name=name, providers=providers())


def rerank(query: str, docs: list[str], model: str | None = None) -> list[float]:
    """Relevance score per doc. Higher is better; scale is model-specific, so these
    are only comparable within one call."""
    if not docs:
        return []
    name = model or settings.rerank_model
    return list(_encoder(name).rerank(query, docs))


def apply(query: str, rows: list[dict], texts: list[str],
          model: str | None = None) -> list[dict]:
    """Reorder `rows` by cross-encoder score, keeping the retrieval score alongside.

    The retrieval score is kept rather than discarded so a later stage can see whether
    the two disagreed -- useful signal when deciding to abstain.
    """
    if not rows:
        return rows
    scores = rerank(query, texts, model)
    for row, s in zip(rows, scores, strict=True):
        row["retrieval_score"] = row.get("score", 0.0)
        row["rerank_score"] = float(s)
    return sorted(rows, key=lambda r: -r["rerank_score"])
