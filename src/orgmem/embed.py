"""Embeddings, local only.

A local model keeps private text off third-party embedding services, which matters for
a system whose whole premise is that the content is access-controlled. fastembed runs
BGE on onnxruntime, so there is no torch dependency.
"""

from __future__ import annotations

import functools

import numpy as np

from .config import settings


@functools.lru_cache(maxsize=4)
def _model(name: str):
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=name)


def embed_texts(texts: list[str], model: str | None = None) -> np.ndarray:
    """(n, dim) float32, L2-normalised so cosine distance == dot product."""
    name = model or settings.embed_model
    if not texts:
        return np.zeros((0, settings.embed_dim), dtype="float32")
    vecs = np.array(list(_model(name).embed(texts)), dtype="float32")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-9, None)


def embed_query(text: str, model: str | None = None) -> np.ndarray:
    """BGE models expect a prefix on the query side but not the document side."""
    name = model or settings.embed_model
    prefix = "Represent this sentence for searching relevant passages: " \
        if "bge" in name.lower() else ""
    return embed_texts([prefix + text], name)[0]


def asset_text(name: str, summary: str | None, aliases: list[str],
               claim_bodies: list[str]) -> str:
    """What actually gets embedded for an asset.

    Claim bodies are included because the useful query is usually about behaviour
    ("rejects payloads over 1MB"), not about a package name.
    """
    parts = [name]
    if aliases:
        parts.append(" ".join(a for a in aliases if a != name))
    if summary:
        parts.append(summary)
    parts.extend(claim_bodies[:20])
    return " . ".join(p for p in parts if p)[:4000]
