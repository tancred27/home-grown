"""The requirements judge: verdicts and order from marks, ties, and failing safe.
No live model: `structured` is replaced by fakes."""

from __future__ import annotations

import homegrown.llm as llm
from homegrown import search as S
from homegrown.search import (
    judge_requirements,
    order_by_requirements,
    requirement_verdict,
    shown_with_ties,
)

REQS = ["multi-tenant", "self-service"]
CARDS = [{"name": "cluster-api", "summary": "Creates Kubernetes clusters", "evidence": []},
         {"name": "tenant-portal", "summary": "Self-service portal", "evidence": []},
         {"name": "clock-panel", "summary": "A clock", "evidence": []}]


def st(*states):
    return [{"state": s, "quote": "" if s == "not_mentioned" else f"q{i}"}
            for i, s in enumerate(states)]


def test_verdicts_follow_the_marks():
    v = lambda *s: requirement_verdict(st(*s), "cluster service", REQS)[:2]   # noqa: E731
    assert v("shown", "shown", "shown") == ("use_as_is", (0, -2, False))
    assert v("shown", "shown", "not_mentioned") == ("extend", (0, -1, False))
    assert v("shown", "shown", "contradicted") == ("extend", (0, -1, True))
    # The loosened gate: kind not settled, but a requirement shown and nothing against.
    assert v("not_mentioned", "shown", "not_mentioned") == ("unknown", (1, -1, False))
    assert v("not_mentioned", "not_mentioned", "not_mentioned") == ("not_a_fit", None)
    assert v("not_mentioned", "shown", "contradicted") == ("not_a_fit", None)
    assert v("contradicted", "shown", "shown") == ("not_a_fit", None)
    assert requirement_verdict(st("shown"), "cluster service", [])[0] == "use_as_is"


def test_reason_names_shown_contradicted_and_missing_requirements():
    _, _, why = requirement_verdict(st("shown", "contradicted", "not_mentioned"),
                                    "cluster service", REQS)
    assert why.startswith('is a cluster service ("q0")')
    assert 'contradicts: multi-tenant ("q1")' in why and "not stated: self-service" in why


def test_order_ranks_ties_and_puts_unkeyed_last():
    keys = [(1, -2, False), (0, -1, False), None, (0, -1, False), (0, -2, False)]
    got = order_by_requirements(list("abcde"), keys)
    assert got == [("e", 1), ("b", 2), ("d", 2), ("a", 4), ("c", None)]


def test_show_three_more_only_on_a_tie_with_the_third():
    assert shown_with_ties([1, 2, 3, 4, 5], 3, 5) == 3
    assert shown_with_ties([1, 2, 3, 3, 3], 3, 5) == 5
    assert shown_with_ties([1, 1, 1, 1, 1, 1], 3, 5) == 5, "never more than top"
    assert shown_with_ties([1, 2, 2, 4], 3, 5) == 3, "a tie before the third adds nothing"
    assert shown_with_ties([1, 2], 3, 5) == 2
    assert shown_with_ties([1, 2, 3, None], 3, 5) == 3
    assert shown_with_ties([None] * 5, 3, 5) == 5, "judge failed: old behaviour"
    assert shown_with_ties([], 3, 5) == 0


def _fake(out, seen=None):
    def fake(system, user, schema, max_tokens=2000, model=None):
        if seen is not None:
            seen.update(user=user, schema=schema, model=model, system=system)
        return out
    return fake


def test_judge_maps_letters_and_orders_by_code(monkeypatch):
    seen = {}
    monkeypatch.setattr(S.settings, "judge_model", "judge-x")
    monkeypatch.setattr(llm, "structured", _fake({
        "kind": "cluster service", "requirements": REQS,
        "marks": [{"candidate": "C", "states": st("contradicted", "not_mentioned",
                                                  "not_mentioned")},
                  {"candidate": "A", "states": st("shown", "shown", "not_mentioned")},
                  {"candidate": "Candidate B", "states": st("not_mentioned", "shown",
                                                            "not_mentioned")}]}, seen))
    split, judged = judge_requirements("multi-tenant cluster service, self-service", CARDS)
    assert split == {"kind": "cluster service", "requirements": REQS}
    assert [j["verdict"] for j in judged] == ["extend", "unknown", "not_a_fit"]
    assert judged[0]["key"] == (0, -1, False) and judged[2]["key"] is None
    assert seen["model"] == "judge-x"
    assert seen["schema"]["properties"]["marks"]["items"]["properties"]["candidate"][
        "enum"] == ["A", "B", "C"]
    assert "Request: multi-tenant" in seen["user"] and "Candidate C: clock-panel" in seen["user"]
    assert "not instructions" in seen["system"]


def test_incomplete_or_missing_marks_are_unknown_not_a_guess(monkeypatch):
    monkeypatch.setattr(llm, "structured", _fake({
        "kind": "cluster service", "requirements": REQS,
        "marks": [{"candidate": "A", "states": st("shown", "shown")},          # one short
                  {"candidate": "B", "states": st("shown", "shown", "shown")}]}))
    _, judged = judge_requirements("x", CARDS)
    assert [j["verdict"] for j in judged] == ["unknown", "use_as_is", "unknown"]
    assert judged[0]["key"] is None and judged[2]["reason"] == "judge returned no complete marks"


def test_any_failure_fails_safe_to_unknown(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("credit balance is too low")
    monkeypatch.setattr(llm, "structured", boom)
    split, judged = judge_requirements("x", CARDS)
    assert split is None and all(j["verdict"] == "unknown" and j["key"] is None
                                 for j in judged)
    assert "unavailable" in judged[0]["reason"]
    monkeypatch.setattr(llm, "structured", _fake({
        "kind": "k", "requirements": [f"r{i}" for i in range(S.MAX_REQUIREMENTS + 1)],
        "marks": []}))
    assert all(j["verdict"] == "unknown" for j in judge_requirements("x", CARDS)[1])
    monkeypatch.setattr(llm, "structured", _fake({"kind": " ", "requirements": [],
                                                  "marks": []}))
    assert all(j["verdict"] == "unknown" for j in judge_requirements("x", CARDS)[1])
