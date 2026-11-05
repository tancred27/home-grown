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


def test_groq_provider_routes_and_defaults(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "groq")
    monkeypatch.setattr(llm.settings, "llm_model", "")
    assert llm._base() == llm.GROQ_BASE
    assert llm.resolve_model() == "openai/gpt-oss-120b"
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(llm.settings, "groq_key_file", "")
    with pytest.raises(llm.LLMUnavailable):
        llm._headers()


def test_daily_quota_429_fails_at_once_without_retrying(monkeypatch):
    """A per-day cap does not clear in seconds; retrying it stalled every call ~9 min."""
    import httpx
    calls, slept = [], []
    body = ('{"error":{"message":"Rate limit reached for model `m` in organization '
            '`org_secret123` service tier `on_demand` on tokens per day (TPD): '
            'Limit 200000, Used 199604","code":"rate_limit_exceeded"}}')

    def fake_post(*a, **k):
        calls.append(1)
        return httpx.Response(429, text=body)
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(llm, "_headers", lambda: {})
    with pytest.raises(llm.QuotaExhausted) as e:
        llm._post_with_backoff({"model": "m"}, "m")
    assert len(calls) == 1 and not slept
    assert "org_secret123" not in str(e.value), "account ids must not be logged"


def test_per_minute_429_is_still_retried(monkeypatch):
    import httpx
    responses = [httpx.Response(429, text='{"error":{"message":"tokens per minute (TPM)"}}'),
                 httpx.Response(200, json={"model": "m", "choices": []})]
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "_headers", lambda: {})
    assert llm._post_with_backoff({"model": "m"}, "m")["model"] == "m"
