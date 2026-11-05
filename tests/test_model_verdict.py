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


def test_labels_like_candidate_a_map_to_the_right_candidate(monkeypatch):
    """Regression: 'Candidate B' was parsed by its first character, 'C'."""
    seen = {}

    def fake(system, user, schema, max_tokens=2000):
        seen["enum"] = schema["properties"]["verdicts"]["items"]["properties"][
            "candidate"]["enum"]
        return {"verdicts": [{"candidate": "Candidate B", "verdict": "not_a_fit", "reason": "x"},
                             {"candidate": "(A)", "verdict": "use_as_is", "reason": "y"}]}
    monkeypatch.setattr(llm, "structured", fake)
    out = judge_with_model("add retries", CARDS)
    assert [o["verdict"] for o in out] == ["use_as_is", "not_a_fit"]
    assert seen["enum"] == ["A", "B"], "the schema must allow only the letters shown"


def test_unparseable_label_is_unknown_not_a_guess(monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda *a, **k: {"verdicts": [
        {"candidate": "the first one", "verdict": "use_as_is", "reason": "y"}]})
    assert all(o["verdict"] == "unknown" for o in judge_with_model("add retries", CARDS))


def test_fifteen_candidate_shortlist_does_not_crash(monkeypatch):
    """Regression: labels stopped at J, so a 15-card shortlist crashed find_existing."""
    cards = [{"name": f"c{i}", "summary": "", "evidence": []} for i in range(15)]

    def fake(system, user, schema, max_tokens=2000):
        enum = schema["properties"]["verdicts"]["items"]["properties"]["candidate"]["enum"]
        assert enum[-1] == "O" and "Candidate O: c14" in user
        return {"verdicts": [{"candidate": "O", "verdict": "use_as_is", "reason": "y"}]}
    monkeypatch.setattr(llm, "structured", fake)
    out = judge_with_model("x", cards)
    assert len(out) == 15 and out[14]["verdict"] == "use_as_is"
    assert out[0]["verdict"] == "unknown"


def test_bad_card_fails_safe_instead_of_raising(monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda *a, **k: {"verdicts": []})
    out = judge_with_model("x", [{"summary": "no name key"}])     # malformed card
    assert out[0]["verdict"] == "unknown"


def test_candidate_o_label_parses():
    from orgmem.search import _candidate_letter
    letters = list("ABCDEFGHIJKLMNO")
    assert _candidate_letter("Candidate O", letters, []) == "O"
    assert _candidate_letter("(m)", letters, []) == "M"
    assert _candidate_letter("Candidate P", letters, []) is None, "P was not shown"
