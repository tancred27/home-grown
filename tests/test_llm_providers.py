"""The provider layer, without network: key files, JSON handling, fallbacks."""

from __future__ import annotations

import pytest

import orgmem.llm as llm


def test_key_file_skips_comment_lines(tmp_path):
    f = tmp_path / "k.txt"
    f.write_text("# paste your key below\n# another comment\n\nAIzaFAKEKEY123  \n")
    assert llm._read_key_file(str(f)) == "AIzaFAKEKEY123"


def test_key_file_with_only_comments_has_no_key(tmp_path):
    f = tmp_path / "k.txt"
    f.write_text("# paste your key below\n\n")
    assert llm._read_key_file(str(f)) is None


def test_strip_fences():
    assert llm._strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert llm._strip_fences('{"a": 1}') == '{"a": 1}'


SCHEMA = {"type": "object", "required": ["verdicts"], "properties": {"verdicts": {
    "type": "array", "items": {"type": "object", "required": ["verdict"], "properties": {
        "verdict": {"type": "string", "enum": ["use_as_is", "not_a_fit"]}}}}}}


def test_check_rejects_malformed_answers():
    llm._check({"verdicts": [{"verdict": "use_as_is"}]}, SCHEMA)
    with pytest.raises(ValueError):
        llm._check({"verdicts": [{"verdict": "definitely"}]}, SCHEMA)   # not in enum
    with pytest.raises(ValueError):
        llm._check({"nope": []}, SCHEMA)                                  # missing key


def test_missing_gemini_key_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(llm.settings, "gemini_key_file", "")
    with pytest.raises(llm.LLMUnavailable):
        llm._gemini_key()


def test_judge_fails_safe_when_the_provider_is_down(monkeypatch):
    from orgmem.search import judge_with_model
    monkeypatch.setattr(llm.settings, "llm_provider", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(llm.settings, "gemini_key_file", "")
    out = judge_with_model("add retries", [{"name": "x", "summary": "y", "evidence": []}])
    assert out[0]["verdict"] == "unknown"
