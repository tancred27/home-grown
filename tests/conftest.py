"""Tests never call a real model.

`.env` may point the app at a live provider (Groq, Gemini) with the model verdict on.
Left alone, every test that runs a search would then make a real API call: the leakage
test alone searches five queries per user, and one run spent ~105 of the free tier's
1,000 daily requests before it was stopped. So, for the whole session:

  * verdict_mode = "rules"        deterministic, no network
  * llm_provider = "disabled"     any accidental call raises LLMUnavailable, which every
                                  caller already treats as "no judgment" (fail safe)

Tests that exercise model behaviour monkeypatch `homegrown.llm.structured` with a fake.
"""

from __future__ import annotations

import pytest

from homegrown.config import settings


@pytest.fixture(autouse=True, scope="session")
def _no_live_llm():
    saved = (settings.verdict_mode, settings.llm_provider)
    settings.verdict_mode = "rules"
    settings.llm_provider = "disabled"
    yield
    settings.verdict_mode, settings.llm_provider = saved
