"""The H-all retrieval (BM25 + top-2 pooled vectors + reciprocal-rank fusion): the pieces
behave as scripts/gate1_harness.py defines them. No model is called."""

from __future__ import annotations

import uuid

import pytest

from homegrown.search import _rrf


def test_rrf_sums_reciprocal_ranks():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sem = [(a, "a", 0.9), (b, "b", 0.8)]
    lex = [(b, "b", 5.0), (c, "c", 4.0)]
    out = _rrf(sem, lex)
    assert [r["asset_id"] for r in out] == [b, a, c]   # b: 1/62 + 1/61 beats a: 1/61
    assert abs(out[0]["score"] - (1 / 62 + 1 / 61)) < 1e-12
    assert out[0]["semantic"] == 0.8 and out[0]["lexical"] == 5.0
    assert out[2]["semantic"] is None


def test_rrf_ties_keep_vector_list_first():
    a, b = uuid.uuid4(), uuid.uuid4()
    out = _rrf([(a, "a", 0.5)], [(b, "b", 1.0)])
    assert [r["asset_id"] for r in out] == [a, b]


def test_rrf_handles_empty_lists():
    assert _rrf([], []) == []


def test_rrf_adds_the_code_words_list_at_its_weight():
    from homegrown.search import _rrf
    sem = [("a", "A", 0.9), ("b", "B", 0.8)]
    lex = [("a", "A", 3.0)]
    code = [("c", "C", 5.0), ("b", "B", 1.0)]
    out = _rrf(sem, lex, code=code, code_weight=1.0)
    score = {r["asset_id"]: r["score"] for r in out}
    assert score["b"] == pytest.approx(1 / 62 + 1 / 62)
    assert score["c"] == pytest.approx(1 / 61)
    assert next(r for r in out if r["asset_id"] == "c")["code"] == 5.0
    off = _rrf(sem, lex, code=code, code_weight=0.0)
    assert {r["asset_id"]: r["score"] for r in off}["c"] == 0.0
    assert [r["asset_id"] for r in _rrf(sem, lex)] == ["a", "b"]
