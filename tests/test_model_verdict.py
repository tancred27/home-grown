"""The model-judged verdict: mapping, and above all that it fails safe."""

from __future__ import annotations

import orgmem.llm as llm
from orgmem.search import judge_with_model

CARDS = [{"name": "retry-client", "summary": "HTTP retries", "evidence": ["test: retries"]},
         {"name": "clock-panel", "summary": "A clock", "evidence": []}]


def test_maps_verdicts_by_letter(monkeypatch):
    def fake(system, user, schema, max_tokens=2000):
        assert "Request: add retries" in user and "Candidate B: clock-panel" in user
        return {"verdicts": [{"candidate": "B", "verdict": "not_a_fit", "reason": "x"},
                             {"candidate": "A", "verdict": "use_as_is", "reason": "y"}]}
    monkeypatch.setattr(llm, "structured", fake)
    out = judge_with_model("add retries", CARDS)
    assert [o["verdict"] for o in out] == ["use_as_is", "not_a_fit"]


def test_missing_candidate_is_unknown(monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda *a, **k: {"verdicts": [
        {"candidate": "A", "verdict": "extend", "reason": "y"}]})
    out = judge_with_model("add retries", CARDS)
    assert out[1]["verdict"] == "unknown"


def test_any_failure_fails_safe_to_unknown(monkeypatch):
    """A broken judge -- no credit, network down, bad JSON -- must never yield reuse."""
    def boom(*a, **k):
        raise RuntimeError("credit balance is too low")
    monkeypatch.setattr(llm, "structured", boom)
    out = judge_with_model("add retries", CARDS)
    assert all(o["verdict"] == "unknown" for o in out)
    assert "unavailable" in out[0]["reason"]
