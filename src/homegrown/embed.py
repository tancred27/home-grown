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

    from .device import providers

    return TextEmbedding(model_name=name, providers=providers())


_RETRIEVE = "Represent this sentence for searching relevant passages: "

# (query prefix, document prefix), from each model's own documentation. Using a model
# without its prefixes is a broken configuration, not a weaker one.
PREFIXES: dict[str, tuple[str, str]] = {
    "BAAI/bge-base-en-v1.5": (_RETRIEVE, ""),
    "BAAI/bge-large-en-v1.5": (_RETRIEVE, ""),
    "mixedbread-ai/mxbai-embed-large-v1": (_RETRIEVE, ""),
    "snowflake/snowflake-arctic-embed-l": (_RETRIEVE, ""),
    "snowflake/snowflake-arctic-embed-m": (_RETRIEVE, ""),
    "thenlper/gte-large": ("", ""),
    "thenlper/gte-base": ("", ""),
    "nomic-ai/nomic-embed-text-v1.5": ("search_query: ", "search_document: "),
}


def _prefixes(name: str) -> tuple[str, str]:
    if name in PREFIXES:
        return PREFIXES[name]
    return (_RETRIEVE, "") if "bge" in name.lower() else ("", "")


def _encode(texts: list[str], name: str) -> np.ndarray:
    if not texts:
        return np.zeros((0, settings.embed_dim), dtype="float32")
    vecs = np.array(list(_model(name).embed(texts)), dtype="float32")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-9, None)


def embed_texts(texts: list[str], model: str | None = None) -> np.ndarray:
    """Documents. (n, dim) float32, L2-normalised so cosine distance == dot product."""
    name = model or settings.embed_model
    _, dp = _prefixes(name)
    return _encode([dp + t for t in texts], name)


def embed_query(text: str, model: str | None = None) -> np.ndarray:
    """Queries get the model's query-side prefix; documents get its document prefix."""
    name = model or settings.embed_model
    qp, _ = _prefixes(name)
    return _encode([qp + text], name)[0]


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
