"""The one place this project calls a language model -- any provider.

Jobs (each must be BLIND to the evaluation):
  * fit judgment: does a candidate let the developer avoid building what they asked for
  * purpose lines: one "what is this for" sentence per unit, from its own source text
  * page triage: is a documentation page a solution / decision / pitfall worth keeping

Providers (settings.llm_provider):
  anthropic   claude-opus-5-5 via the Anthropic SDK (structured output, refusal
              fallbacks). Needs API credit.
  gemini      Google's OpenAI-compatible endpoint. The FREE TIER may use prompts to
              improve Google's products: fine for this project's public repositories,
              never for private code. Production needs the paid tier or `local`.
  groq        Groq's OpenAI-compatible endpoint; default model openai/gpt-oss-120b
              (OpenAI's open-weight model). FREE tier: same rule as Gemini -- public
              repositories only.
  local       any OpenAI-compatible server, e.g. Ollama at http://localhost:11434/v1.
              Nothing leaves the machine.

Repeatability: no sampling parameters are relied on. Outputs that feed an evaluation
are cached on disk (`cached_json`) and committed, so a rerun re-scores the same text.

Every caller treats a raised exception as "no judgment" and fails safe (e.g. the fit
judge then returns `unknown`), so a provider outage can never produce a reuse
recommendation.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path

import httpx

from .config import settings

ANTHROPIC_MODEL = "claude-opus-5-5"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
GROQ_BASE = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"


class LLMRefusal(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- keys & models

def _read_key_file(path: str) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):        # the key file carries # instructions
            return s.split()[0]
    return None


def _gemini_key() -> str:
    import os
    key = os.environ.get("GEMINI_API_KEY") or _read_key_file(settings.gemini_key_file)
    if not key:
        raise LLMUnavailable("no Gemini key: set GEMINI_API_KEY or ORGMEM_GEMINI_KEY_FILE")
    return key


def _groq_key() -> str:
    import os
    key = os.environ.get("GROQ_API_KEY") or _read_key_file(settings.groq_key_file)
    if not key:
        raise LLMUnavailable("no Groq key: set GROQ_API_KEY or ORGMEM_GROQ_KEY_FILE")
    return key


_model_cache: dict[str, str] = {}


def resolve_model() -> str:
    """The model to use. An explicit setting wins; otherwise, for Gemini, pick the newest
    stable 'flash' model the key can actually see -- model names change too often to
    hard-code."""
    if settings.llm_model:
        return settings.llm_model
    p = settings.llm_provider
    if p == "anthropic":
        return ANTHROPIC_MODEL
    if p == "groq":
        return GROQ_DEFAULT_MODEL
    if p in _model_cache:
        return _model_cache[p]
    if p == "gemini":
        r = httpx.get(f"{GEMINI_BASE}/models", headers=_headers(), timeout=30)
        r.raise_for_status()
        ids = [m["id"].removeprefix("models/") for m in r.json().get("data", [])]
        flash = [i for i in ids if "flash" in i and not any(
            x in i for x in ("lite", "preview", "exp", "tts", "image", "audio", "live",
                             "thinking", "embedding"))]
        if not flash:
            raise LLMUnavailable(f"no stable flash model visible; got {ids[:8]}")
        _model_cache[p] = sorted(flash, key=_version_key)[-1]
        return _model_cache[p]
    raise LLMUnavailable(f"set ORGMEM_LLM_MODEL for provider {p!r}")


def _version_key(model_id: str):
    import re
    nums = [int(x) for x in re.findall(r"\d+", model_id)]
    return (nums, model_id)


def _base() -> str:
    return {"gemini": GEMINI_BASE, "groq": GROQ_BASE}.get(settings.llm_provider,
                                                          settings.llm_base_url)


def _headers() -> dict:
    if settings.llm_provider == "gemini":
        return {"Authorization": f"Bearer {_gemini_key()}"}
    if settings.llm_provider == "groq":
        return {"Authorization": f"Bearer {_groq_key()}"}
    return {"Authorization": "Bearer local"}


# ---------------------------------------------------------------- the call

def structured(system: str, user: str, schema: dict, max_tokens: int = 2000) -> dict:
    """One call, JSON out, matching `schema`. Raises on any failure (callers fail safe)."""
    if settings.llm_provider == "anthropic":
        return _anthropic(system, user, schema, max_tokens)
    try:
        return _openai_compat(system, user, schema, max_tokens)
    except _Retry:
        # The endpoint rejected json_schema; _openai_compat has switched this model to
        # json_object mode with the schema in the prompt. Try once more that way.
        return _openai_compat(system, user, schema, max_tokens)


def _anthropic(system, user, schema, max_tokens) -> dict:
    import anthropic
    resp = anthropic.Anthropic(max_retries=5).beta.messages.create(
        model=resolve_model(), max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    if resp.stop_reason == "refusal":
        raise LLMRefusal("refused")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("hit max_tokens before the JSON finished")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"no text block (stop_reason={resp.stop_reason})")
    return json.loads(text)


_json_schema_supported: dict[str, bool] = {}
# Which model actually answered (an alias such as *-latest can resolve differently over
# time). Evaluations record it, so a result is tied to a real model, not an alias.
last_model_served: dict[str, str] = {}


def _openai_compat(system, user, schema, max_tokens) -> dict:
    model = resolve_model()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if _json_schema_supported.get(model, True):
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "out", "schema": schema, "strict": True}}
    else:
        body["response_format"] = {"type": "json_object"}
        body["messages"] = [{"role": "system", "content": system +
                             "\n\nReply with JSON only, matching this JSON Schema:\n" +
                             json.dumps(schema)}, messages[1]]
    data = _post_with_backoff(body, model)
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError("hit max_tokens before the JSON finished")
    text = (choice.get("message") or {}).get("content") or ""
    out = json.loads(_strip_fences(text))
    _check(out, schema)
    return out


def _post_with_backoff(body: dict, model: str, attempts: int = 6) -> dict:
    """Free tiers rate-limit hard: honour Retry-After, back off exponentially on 429/5xx.
    A 400 on response_format downgrades to json_object mode once, then retries."""
    delay = 4.0
    for i in range(attempts):
        try:
            r = httpx.post(f"{_base()}/chat/completions", headers=_headers(), json=body,
                           timeout=120)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            # A slow or dropped connection is retryable, not a reason to give up at once.
            if i < attempts - 1:
                time.sleep(delay + random.uniform(0, 1))
                delay *= 2
                continue
            raise LLMUnavailable(f"{type(e).__name__} after {attempts} attempts") from e
        if r.status_code == 200:
            data = r.json()
            last_model_served[model] = data.get("model", model)
            return data
        if r.status_code == 400 and "response_format" in body and \
                body["response_format"].get("type") == "json_schema":
            _json_schema_supported[model] = False
            raise _Retry()
        if r.status_code in (429, 500, 502, 503, 504) and i < attempts - 1:
            ra = r.headers.get("retry-after")
            wait = float(ra) if ra and ra.replace(".", "").isdigit() else delay
            time.sleep(min(wait, 90) + random.uniform(0, 1))
            delay *= 2
            continue
        raise LLMUnavailable(f"{r.status_code}: {r.text[:300]}")
    raise LLMUnavailable("retries exhausted")


class _Retry(Exception):
    pass


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.rsplit("```", 1)[0]
    return t.strip()


def _check(out, schema: dict) -> None:
    """Light structural validation: required keys present, enums respected. Enough to
    reject a malformed answer so the caller fails safe instead of acting on it."""
    if schema.get("type") == "object":
        if not isinstance(out, dict):
            raise ValueError("expected an object")
        for k in schema.get("required", []):
            if k not in out:
                raise ValueError(f"missing key {k!r}")
        for k, sub in (schema.get("properties") or {}).items():
            if k in out:
                _check(out[k], sub)
    elif schema.get("type") == "array":
        if not isinstance(out, list):
            raise ValueError("expected an array")
        for x in out:
            _check(x, schema.get("items") or {})
    elif "enum" in schema and out not in schema["enum"]:
        raise ValueError(f"{out!r} not in {schema['enum']}")


def cached_json(cache_file: Path, key_parts: list[str], make):
    """Memoise `make()` on disk, keyed by a hash of its inputs. Committed alongside the
    experiment, so a rerun scores the identical text and the cost is paid once."""
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
