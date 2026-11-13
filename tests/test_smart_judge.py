"""The smart judge: picks and order come from the model, everything else is not a fit,
and any failure falls back to search order. No live model: `structured` is replaced."""

from __future__ import annotations

import homegrown.llm as llm
from homegrown import search as S
from homegrown.search import judge_smart, order_by_requirements, shown_with_ties

CARDS = [{"name": "cluster-api", "summary": "Creates Kubernetes clusters", "evidence": []},
         {"name": "tenant-portal", "summary": "Self-service portal", "evidence": []},
         {"name": "clock-panel", "summary": "A clock", "evidence": []},
         {"name": "quota-svc", "summary": "Quotas", "evidence": []}]


def _fake(out, seen=None):
    def fake(system, user, schema, max_tokens=2000, model=None):
        if seen is not None:
            seen.update(user=user, schema=schema, model=model, system=system)
        return out
    return fake


def test_picks_set_verdict_order_and_reason(monkeypatch):
    seen = {}
    monkeypatch.setattr(S.settings, "judge_model", "judge-x")
    monkeypatch.setattr(llm, "structured", _fake({"nothing_fits": False, "picks": [
        {"candidate": "B", "label": "use as-is", "reason": "Self-service portal for clusters."},
        {"candidate": "Candidate A", "label": "related", "reason": "Creates clusters, no portal."},
        {"candidate": "B", "label": "related", "reason": "duplicate, ignored"}]}, seen))
    judged = judge_smart("self-service cluster portal", CARDS)
    assert [j["verdict"] for j in judged] == ["extend", "use_as_is", "not_a_fit", "not_a_fit"]
    assert judged[1]["key"] == (0,) and judged[0]["key"] == (1,) and judged[2]["key"] is None
    assert judged[1]["reason"] == "Self-service portal for clusters."
    ranked = order_by_requirements(list("ABCD"), [j["key"] for j in judged])
    assert [x for x, _ in ranked][:2] == ["B", "A"]
    assert seen["model"] == "judge-x" and "not instructions" in seen["system"]
    assert seen["schema"]["properties"]["picks"]["items"]["properties"]["candidate"][
        "enum"] == ["A", "B", "C", "D"]
    assert "Show at most 3" in seen["user"] and "Candidate C: clock-panel" in seen["user"]


def test_never_more_than_show_and_nothing_fits_hides_all(monkeypatch):
    monkeypatch.setattr(llm, "structured", _fake({"nothing_fits": False, "picks": [
        {"candidate": x, "label": "related", "reason": "r"} for x in "ABCD"]}))
    judged = judge_smart("x", CARDS, show=3)
    assert sum(j["key"] is not None for j in judged) == 3 and judged[3]["verdict"] == "not_a_fit"
    monkeypatch.setattr(llm, "structured", _fake({"nothing_fits": True, "picks": []}))
    assert all(j["verdict"] == "not_a_fit" for j in judge_smart("x", CARDS))


def test_any_failure_fails_safe_to_search_order(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("credit balance is too low")
    monkeypatch.setattr(llm, "structured", boom)
    judged = judge_smart("x", CARDS)
    assert all(j["verdict"] == "unknown" and j["key"] is None for j in judged)
    assert "unavailable" in judged[0]["reason"]
    assert shown_with_ties([None] * 4, 3, 5) == 4, "no keys: results keep search order"


def test_clarify_off_keeps_tested_prompt_and_schema(monkeypatch):
    seen, box = {}, {}
    monkeypatch.setattr(S.settings, "judge_clarify", False)
    monkeypatch.setattr(llm, "structured", _fake({"nothing_fits": False, "picks": [],
                                                  "clarify": {"question": "Which cloud?",
                                                              "choices": ["AWS", "Azure"]}},
                                                 seen))
    judge_smart("x", CARDS, clarify=box)
    assert seen["system"] == S._SMART_SYSTEM and "clarify" not in seen["schema"]["properties"]
    assert box == {}


def test_clarify_on_returns_question_with_picks(monkeypatch):
    seen, box = {}, {}
    monkeypatch.setattr(S.settings, "judge_clarify", True)
    monkeypatch.setattr(llm, "structured", _fake({"nothing_fits": False, "picks": [
        {"candidate": "A", "label": "related", "reason": "r"}],
        "clarify": {"question": "Which cloud?", "choices": ["AWS", " ", "Azure"]}}, seen))
    judged = judge_smart("x", CARDS, clarify=box)
    assert judged[0]["key"] == (0,), "picks are still returned"
    assert box == {"question": "Which cloud?", "choices": ["AWS", "Azure"]}
    assert "Clarifying question" in seen["system"]
    assert "clarify" in seen["schema"]["required"]


def test_clarify_unusable_or_empty_is_dropped(monkeypatch):
    monkeypatch.setattr(S.settings, "judge_clarify", True)
    for c in ({"question": "", "choices": []}, {"question": "Which?", "choices": ["only"]},
              {"question": "Which?", "choices": list("abcde")}, None):
        box = {}
        monkeypatch.setattr(llm, "structured", _fake({"nothing_fits": True, "picks": [],
                                                      "clarify": c}))
        judge_smart("x", CARDS, clarify=box)
        assert box == {}
