"""The replay provider and the per-call model override. No live model is called."""

from __future__ import annotations

import json

import pytest

from homegrown import llm
from homegrown.config import settings

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}},
          "required": ["summary"], "additionalProperties": False}


def test_replay_serves_recorded_output_and_logs_missing(tmp_path, monkeypatch):
    f = tmp_path / "rec.json"
    monkeypatch.setattr(settings, "llm_provider", "replay")
    monkeypatch.setattr(settings, "replay_file", str(f))
    key = llm.replay_key("sys", "user text", SCHEMA)
    f.write_text(json.dumps({key: {"summary": "recorded"}}), encoding="utf-8")
    assert llm.structured("sys", "user text", SCHEMA) == {"summary": "recorded"}
    with pytest.raises(llm.LLMUnavailable):
        llm.structured("sys", "another prompt", SCHEMA, model="claude-haiku-5-5")
    log = (tmp_path / "rec.json.missing.jsonl").read_text("utf-8")
    missing = [json.loads(x) for x in log.splitlines()]
    assert missing[0]["user"] == "another prompt" and missing[0]["model"] == "claude-haiku-5-5"


def test_model_override_lasts_one_call(monkeypatch):
    seen = []
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_model", "configured-model")
    monkeypatch.setattr(llm, "_call",
                        lambda *a: seen.append(llm.resolve_model()) or {"summary": "x"})
    llm.structured("s", "u", SCHEMA, model="claude-haiku-5-5")
    llm.structured("s", "u", SCHEMA)
    assert seen == ["claude-haiku-5-5", "configured-model"]
