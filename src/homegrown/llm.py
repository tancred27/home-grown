"""The one place this project calls a language model.

Used for: judging whether a candidate fits a request, writing a unit's one-line
purpose, deciding whether a documentation page is worth keeping, and rewriting queries.

Providers (settings.llm_provider):
  anthropic   Claude via the Anthropic SDK.
  gemini      Google's OpenAI-compatible endpoint.
  groq        Groq's OpenAI-compatible endpoint (default model openai/gpt-oss-120b).
  local       any OpenAI-compatible server, e.g. Ollama. Nothing leaves the machine.
  disabled    every call raises LLMUnavailable.

Free tiers (Gemini, Groq) may use prompts for training: public code only.

Every function here raises on failure, and every caller treats that as "no answer"
and falls back to something safe (the judge returns `unknown`, and so on).
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

import httpx

from . import telemetry
from .config import settings

ANTHROPIC_MODEL = "claude-opus-5-5"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
GROQ_BASE = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"


class LLMRefusal(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


class QuotaExhausted(LLMUnavailable):
    """The provider's per-day cap is spent. Not retried; evaluation scripts stop on it."""


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


def _key(provider: str) -> str:
    """The API key for 'gemini' or 'groq', from <PROVIDER>_API_KEY or the key file."""
    env = f"{provider.upper()}_API_KEY"
    key = os.environ.get(env) or _read_key_file(getattr(settings, f"{provider}_key_file"))
    if not key:
        raise LLMUnavailable(f"no {provider} key: set {env} or "
                             f"HOMEGROWN_{provider.upper()}_KEY_FILE")
    return key


_model_cache: dict[str, str] = {}


# A per-call model chosen by the caller (for example Haiku for summaries, Sonnet for the
# crawler's compiler). Set only for the duration of one structured() call.
_model_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "model_override", default=None)


def resolve_model() -> str:
    """The model to use. An explicit setting wins; otherwise, for Gemini, pick the newest
    stable 'flash' model the key can actually see -- model names change too often to
    hard-code."""
    if _model_override.get():
        return _model_override.get()
    if settings.llm_model:
        return settings.llm_model
    p = settings.llm_provider
    if p == "replay":
        return "replay"
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
    raise LLMUnavailable(f"set HOMEGROWN_LLM_MODEL for provider {p!r}")


def _version_key(model_id: str):
    nums = [int(x) for x in re.findall(r"\d+", model_id)]
    return (nums, model_id)


def _base() -> str:
    return {"gemini": GEMINI_BASE, "groq": GROQ_BASE}.get(settings.llm_provider,
                                                          settings.llm_base_url)


def _headers() -> dict:
    if settings.llm_provider in ("gemini", "groq"):
        return {"Authorization": f"Bearer {_key(settings.llm_provider)}"}
    return {"Authorization": "Bearer local"}


# ---------------------------------------------------------------- the call

def structured(system: str, user: str, schema: dict, max_tokens: int = 2000,
               model: str | None = None) -> dict:
    """One call, JSON out, matching `schema`. Raises on any failure (callers fail safe).

    `model` overrides the configured model for this call only (the crawler uses a cheap model
    for summaries and a stronger one for its compiler)."""
    token = _model_override.set(model) if model else None
    try:
        return _structured(system, user, schema, max_tokens)
    finally:
        if token is not None:
            _model_override.reset(token)


def _structured(system: str, user: str, schema: dict, max_tokens: int) -> dict:
    provider = settings.llm_provider
    # The span and metrics carry provider, model, tokens and outcome; never the prompt
    # or the answer.
    with telemetry.span("llm.call", {"gen_ai.provider.name": provider,
                                     "gen_ai.request.max_tokens": max_tokens}) as sp:
        t0 = time.monotonic()
        model, outcome = "", "error"
        try:
            if provider == "disabled":
                raise LLMUnavailable("LLM disabled (llm_provider=disabled)")
            model = resolve_model()
            sp.set_attribute("gen_ai.request.model", model)
            out = _call(provider, system, user, schema, max_tokens)
            outcome = "ok"
            return out
        except LLMRefusal:
            outcome = "refusal"
            raise
        except QuotaExhausted:
            outcome = "quota"
            raise
        except LLMUnavailable:
            outcome = "unavailable"
            raise
        finally:
            sp.set_attributes({"homegrown.llm.outcome": outcome,
                               "homegrown.llm.latency_s": time.monotonic() - t0})
            telemetry.LLM_CALLS.add(1, {"provider": provider, "model": model,
                                        "outcome": outcome})


def _call(provider, system, user, schema, max_tokens) -> dict:
    if provider == "replay":
        return _replay(system, user, schema)
    if provider == "anthropic":
        return _anthropic(system, user, schema, max_tokens)
    try:
        return _openai_compat(system, user, schema, max_tokens)
    except _Retry:
        # The endpoint rejected json_schema; _openai_compat has switched this model to
        # json_object mode with the schema in the prompt. Try once more that way.
        telemetry.annotate({"homegrown.llm.json_object_fallback": True})
        return _openai_compat(system, user, schema, max_tokens)


def _record_usage(model: str, input_tokens, output_tokens) -> None:
    """Token counts, when the provider reports them."""
    for kind, n in (("input", input_tokens), ("output", output_tokens)):
        if isinstance(n, int):
            telemetry.annotate({f"gen_ai.usage.{kind}_tokens": n})
            telemetry.LLM_TOKENS.add(n, {"model": model, "type": kind})


def _anthropic(system, user, schema, max_tokens) -> dict:
    import anthropic
    # With our telemetry on, the SDK's own spans stay off: ours already cover the call,
    # and its content mode could be switched on from the environment.
    otel = {"open_telemetry": {"traces": {"enabled": False}, "propagation": False}} \
        if telemetry.enabled() else {}
    resp = anthropic.Anthropic(max_retries=5, **otel).beta.messages.create(
        model=resolve_model(), max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": schema}},
        **({} if "haiku" in resolve_model() else      # Haiku has no server-side fallback
           {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}))
    usage = getattr(resp, "usage", None)
    _record_usage(resolve_model(), getattr(usage, "input_tokens", None),
                  getattr(usage, "output_tokens", None))
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
        telemetry.annotate({"homegrown.llm.retries": i})
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
            usage = data.get("usage") or {}
            _record_usage(model, usage.get("prompt_tokens"), usage.get("completion_tokens"))
            return data
        if r.status_code == 400 and "response_format" in body and \
                body["response_format"].get("type") == "json_schema":
            _json_schema_supported[model] = False
            raise _Retry()
        if r.status_code == 429 and _DAILY_LIMIT.search(r.text):
            # A per-day cap won't clear in seconds; retrying would only stall the caller.
            raise QuotaExhausted(_quota_message(r.text))
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


# Groq: "tokens per day (TPD)" / "requests per day (RPD)"; Gemini: "...PerDay...".
_DAILY_LIMIT = re.compile(r"per day|\((?:TPD|RPD)\)|PerDay", re.I)


def _quota_message(text: str) -> str:
    try:
        msg = json.loads(text)["error"]["message"]
    except Exception:
        msg = text
    msg = re.sub(r"organization `[^`]*`\s*", "", msg)       # don't log account ids
    return f"daily quota exhausted: {msg[:200]}"


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
    """Memoise `make()` on disk, keyed by a hash of its inputs. Evaluation scripts use
    it so a rerun scores the same model output without paying for it again."""
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

# ---------------------------------------------------------------- replay provider
# For development and evaluation without API credits: outputs recorded elsewhere (for
# example by Claude agents given the exact same prompts) are served by prompt hash. A
# missing recording is logged to <replay_file>.missing.jsonl and fails safe like any
# unavailable model. Tests and production never need it.

def replay_key(system: str, user: str, schema: dict) -> str:
    raw = json.dumps([system, user, schema], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _replay(system: str, user: str, schema: dict) -> dict:
    if not settings.replay_file:
        raise LLMUnavailable("replay provider needs HOMEGROWN_REPLAY_FILE")
    path = Path(settings.replay_file)
    key = replay_key(system, user, schema)
    store = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if key in store:
        out = store[key]
        _check(out, schema)
        return out
    missing = Path(str(path) + ".missing.jsonl")
    with missing.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "model": _model_override.get() or settings.llm_model,
                            "system": system, "user": user, "schema": schema},
                           ensure_ascii=False) + "\n")
    raise LLMUnavailable(f"no replay recording for {key}")
