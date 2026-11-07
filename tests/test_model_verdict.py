"""The model-judged verdict: mapping, and above all that it fails safe."""

from __future__ import annotations

import homegrown.llm as llm
from homegrown.search import judge_with_model

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
    from homegrown.search import _candidate_letter
    letters = list("ABCDEFGHIJKLMNO")
    assert _candidate_letter("Candidate O", letters, []) == "O"
    assert _candidate_letter("(m)", letters, []) == "M"
    assert _candidate_letter("Candidate P", letters, []) is None, "P was not shown"


def test_query_rewrite_joins_the_search_and_fails_safe(monkeypatch):
    import homegrown.search as S
    monkeypatch.setattr(S.settings, "verdict_mode", "model")
    monkeypatch.setattr(S.settings, "query_rewrite", True)
    S._rewrite_cache.clear()
    import homegrown.llm as L
    monkeypatch.setattr(L, "structured",
                        lambda *a, **k: {"purpose": "Detect outliers in a group of time series."})
    assert S.rewrite_query("which pod's latency curve looks different?") == \
        "Detect outliers in a group of time series."
    S._rewrite_cache.clear()

    def boom(*a, **k):
        raise L.LLMUnavailable("down")
    monkeypatch.setattr(L, "structured", boom)
    assert S.rewrite_query("anything") is None, "no model: plain search, never an error"
    monkeypatch.setattr(S.settings, "verdict_mode", "rules")
    assert S.rewrite_query("x") is None, "rules mode never calls a model"
