"""The one place this project calls a language model.

Used for two jobs where the model must be BLIND to the evaluation:
  * writing a "what is this for" line from an asset's own README/manifest text
    (never sees a query), and
  * writing the search keywords an agent would add, from the query text alone
    (never sees the corpus).
Blindness is the point: the author of the queries cannot write either without leaking
the answers. See experiments/gate1/RESULTS.md run 4.

Notes on the request shape (current API):
  * No `temperature`: it is removed on current models. Repeatability comes from caching
    every output on disk (`cached_json`) and committing it, so evals re-score the same
    text rather than re-sampling.
  * Thinking cannot be disabled on claude-opus-5-5; effort is set to "low" for these
    short, simple tasks (the model's default is "medium").
  * Structured output via output_config.format, read from the first TEXT block, since a
    (possibly empty) thinking block may precede it.
  * Server-side refusal fallbacks are on, and stop_reason is checked before reading.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import anthropic

MODEL = "claude-opus-5-5"
_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=5)   # 429 / 5xx / connection errors
    return _client


class LLMRefusal(RuntimeError):
    pass


def structured(system: str, user: str, schema: dict, max_tokens: int = 2000) -> dict:
    """One call, JSON out, validated by the API against `schema`."""
    resp = client().beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": schema},
        },
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        cat = getattr(getattr(resp, "stop_details", None), "category", None)
        raise LLMRefusal(f"refused (category={cat})")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("hit max_tokens before the JSON finished")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"no text block (stop_reason={resp.stop_reason})")
    return json.loads(text)


def cached_json(cache_file: Path, key_parts: list[str], make):
    """Memoise `make()` on disk, keyed by a hash of its inputs.

    Stored alongside the experiment and committed, so a rerun scores the identical text
    and the cost is paid once. Change any input and the key changes.
    """
    cache = {}
    if cache_file.is_file():
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    key = hashlib.sha256("\x1f".join(key_parts).encode("utf-8")).hexdigest()[:20]
    if key in cache:
        return cache[key]["out"]
    out = make()
    cache[key] = {"in": key_parts, "out": out}
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
