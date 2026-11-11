"""Telemetry: off costs nothing, on produces the expected spans, and never records content.

The SDK tests use in-memory exporters, so no collector is needed. The model is stubbed
at the HTTP layer, so the real llm.structured runs and its spans and metrics are checked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import timedelta

import pytest

from homegrown import telemetry
from homegrown.config import settings
from homegrown.db import admin_conn
from tests.test_state_sync import T0, _claim_for, git, write

QUESTION = "has anyone built qzxv7 retries for failed http calls?"
MARK_OUT = "OUTMARK41"         # in every stubbed model output
MARK_REWRITE = f"Retries failed HTTP requests with backoff {MARK_OUT}"


# ---------------------------------------------------------------- telemetry off

def test_off_by_default_answers_work_without_the_sdk():
    """As if the [otel] extra were not installed: importing the SDK fails. (LangGraph,
    via LangSmith, imports parts of the SDK whenever it is installed, so absence is the
    honest test.) Every entry point still loads, and questions are still answered."""
    login = f"zz-otel-off-{uuid.uuid4().hex[:6]}"
    code = f"""
import sys

class NoSDK:
    def find_spec(self, name, path=None, target=None):
        if name.startswith(("opentelemetry.sdk", "opentelemetry.exporter")):
            raise ModuleNotFoundError(name)

sys.meta_path.insert(0, NoSDK())
from opentelemetry import metrics, trace
from homegrown import cli, telemetry
from homegrown import api, mcp_server  # noqa: F401  every entry point
assert cli.main(["add-user", {login!r}]) == 0
assert cli.main(["find", "retry failed http calls", "--user", {login!r}]) == 0
assert cli.main(["ask", "retry failed http calls", "--user", {login!r}]) == 0
assert telemetry.init("test") is False and not telemetry.enabled()
# Still the API's own no-op providers.
assert type(trace.get_tracer_provider()).__name__ == "ProxyTracerProvider"
assert type(metrics.get_meter_provider()).__name__ == "_ProxyMeterProvider"
"""
    env = {**os.environ, "HOMEGROWN_OTEL_ENABLED": "false",
           "HOMEGROWN_LLM_PROVIDER": "disabled", "HOMEGROWN_VERDICT_MODE": "rules"}
    try:
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                           text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-2000:]
    finally:
        with admin_conn() as conn:
            conn.execute("delete from membership where user_id in "
                         "(select id from app_user where login = %s)", (login,))
            conn.execute("delete from question where asker_id in "
                         "(select id from app_user where login = %s)", (login,))
            conn.execute("delete from app_user where login = %s", (login,))


# ---------------------------------------------------------------- telemetry on

_EXPORTERS: dict = {}


@pytest.fixture
def otel(monkeypatch):
    """In-memory exporter and reader. A process can set its providers only once, so the
    first use initialises them and later uses clear the spans."""
    pytest.importorskip("opentelemetry.sdk", reason="needs the [otel] extra")
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    if not _EXPORTERS:
        monkeypatch.setattr(settings, "otel_enabled", True)
        _EXPORTERS.update(spans=InMemorySpanExporter(), metrics=InMemoryMetricReader())
        assert telemetry.init("test", _EXPORTERS["spans"], _EXPORTERS["metrics"])
    _EXPORTERS["spans"].clear()
    return _EXPORTERS["spans"], _EXPORTERS["metrics"]


@pytest.fixture
def mono(tmp_path):
    """A git repo with two units, as in test_state_sync."""
    name = f"zz-otel-{uuid.uuid4().hex[:6]}"
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "README.md", f"# {name}\n")
    for u, what in (("a", "retries HTTP calls"), ("b", "parses durations")):
        write(repo, f"pkg/{u}/package.json",
              json.dumps({"name": f"{name}-{u}", "description": f"Library that {what}"}))
        write(repo, f"pkg/{u}/README.md", f"# {u}\n\nThis library {what}.\n")
        write(repo, f"pkg/{u}/{u}.test.ts", f"it('handles the basic case for {u}', () => {{}})\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    full = f"testorg/{name}"
    yield full, repo
    with admin_conn() as conn:
        conn.execute("delete from asset where unit_id like %s", (f"github:{name}:%",))
        conn.execute("delete from unit_fingerprint where repo = %s", (full,))
        conn.execute("delete from repo_state where repo = %s", (full,))


class _Resp:
    def __init__(self, status: int, payload: dict | None = None, text: str = ""):
        self.status_code, self._payload, self.text, self.headers = status, payload, text, {}

    def json(self):
        return self._payload


def _fake_groq(url, **kw):
    """A stub provider: a purpose line for the rewrite, verdicts for the judge."""
    schema = kw["json"]["response_format"]["json_schema"]["schema"]
    if "purpose" in schema["properties"]:
        out = {"purpose": MARK_REWRITE}
    elif "marks" in schema["properties"]:           # the requirements judge
        letters = schema["properties"]["marks"]["items"]["properties"]["candidate"]["enum"]
        out = {"kind": "HTTP retry client", "requirements": ["backoff"],
               "marks": [{"candidate": x, "states": [
                   {"state": "shown" if x == "A" else "contradicted",
                    "quote": f"retries HTTP calls {MARK_OUT}"},
                   {"state": "shown", "quote": "backoff"}]} for x in letters]}
    else:
        letters = schema["properties"]["verdicts"]["items"]["properties"]["candidate"]["enum"]
        out = {"verdicts": [{"candidate": x, "verdict": "use_as_is" if x == "A" else
                             "not_a_fit", "reason": f"It retries HTTP calls {MARK_OUT}"}
                            for x in letters]}
    return _Resp(200, {"model": "test-model", "usage": {"prompt_tokens": 120,
                                                       "completion_tokens": 30},
                       "choices": [{"finish_reason": "stop",
                                    "message": {"content": json.dumps(out)}}]})


@pytest.fixture
def model_mode(monkeypatch):
    import homegrown.llm as L
    import homegrown.search as S
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "judge_model", "")   # another provider: no Claude pin
    monkeypatch.setattr(settings, "verdict_mode", "model")
    monkeypatch.setattr(settings, "query_rewrite", True)
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    monkeypatch.setattr(L.httpx, "post", _fake_groq)
    S._rewrite_cache.clear()


@pytest.fixture
def asker():
    from homegrown.collector.directory import org_wide_team
    from homegrown.store import ensure_user, grant_membership
    login = f"zz-otel-{uuid.uuid4().hex[:6]}"
    with admin_conn() as conn:
        everyone = org_wide_team(conn)
        uid = ensure_user(conn, login, everyone)
        grant_membership(conn, uid, everyone)
    yield login, str(uid)
    with admin_conn() as conn:
        conn.execute("delete from membership where user_id = %s", (uid,))
        conn.execute("delete from question where asker_id = %s", (uid,))
        conn.execute("delete from app_user where id = %s", (uid,))


def _by_name(spans) -> dict:
    out: dict = {}
    for s in spans:
        out.setdefault(s.name, []).append(s)
    return out


def _points(reader) -> dict:
    """metric name -> [(attributes, point)]"""
    out: dict = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                out.setdefault(m.name, []).extend(
                    (dict(p.attributes), p) for p in m.data.data_points)
    return out


def _forbidden(login: str, uid: str) -> list[str]:
    """Content and identity that must never reach telemetry."""
    from homegrown.search import _JUDGE_SYSTEM, REWRITE_SYSTEM
    return [QUESTION, "qzxv7", MARK_OUT, "Request:", REWRITE_SYSTEM[:40],
            _JUDGE_SYSTEM[:40], "This library retries", "handles the basic case",
            login, uid]


def test_a_sync_pass_and_a_question_produce_the_expected_spans(otel, mono, asker,
                                                               model_mode):
    from homegrown.answer import ask
    from homegrown.collector.source import LocalGitSource
    from homegrown.collector.sync import Syncer
    spans, _ = otel
    full, repo = mono
    stats = Syncer(LocalGitSource({full: repo})).sync(full, None)
    assert stats["status"] == "processed"

    login, uid = asker
    out = ask(uid, QUESTION)
    assert out["answer"] == "yes", out["text"]

    got = _by_name(spans.get_finished_spans())
    for name in ("sync.pass", "collect.triage", "collect.extract", "collect.apply",
                 "answer.ask", "answer.retrieve_and_judge", "answer.enrich",
                 "answer.compose", "search.find_existing", "search.rewrite_query",
                 "search", "search.embed", "search.lexical", "search.semantic",
                 "search.judge_requirements", "llm.call"):
        assert name in got, f"missing span {name}: {sorted(got)}"

    p = got["sync.pass"][0].attributes
    assert p["homegrown.repo"] == full and p["homegrown.status"] == "processed"
    assert p["homegrown.changed_units"] == 3
    assert got["collect.extract"][0].attributes["homegrown.claims"] > 0

    names = {s.context.span_id: s.name for s in spans.get_finished_spans()}

    def parents(name):
        return {names[s.parent.span_id] for s in got[name]}
    assert parents("search.lexical") == {"search"}
    assert parents("search.semantic") == {"search"}
    assert parents("search") == {"search.find_existing"}
    assert parents("search.find_existing") == {"answer.retrieve_and_judge"}
    assert parents("answer.retrieve_and_judge") == {"answer.ask"}
    # The collector's purpose line is a model call too.
    assert parents("llm.call") == {"search.rewrite_query", "search.judge_requirements",
                                   "collect.update_map"}

    assert got["search.semantic"][0].attributes["homegrown.vector_mode"] in ("exact", "hnsw")
    assert got["search.find_existing"][0].attributes["homegrown.question.length"] == \
        len(QUESTION)
    assert got["answer.ask"][0].attributes["homegrown.answer"] == "yes"
    for call in got["llm.call"]:
        if names[call.parent.span_id] == "collect.update_map":
            continue                    # the stub doesn't speak the purpose-line schema
        a = call.attributes
        assert (a["gen_ai.provider.name"], a["gen_ai.request.model"],
                a["homegrown.llm.outcome"]) == ("groq", "test-model", "ok")
        assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (120, 30)


def test_no_span_attribute_holds_the_question_model_output_or_identity(
        otel, mono, asker, model_mode):
    from homegrown.answer import ask
    from homegrown.collector.source import LocalGitSource
    from homegrown.collector.sync import Syncer
    spans, _ = otel
    full, repo = mono
    Syncer(LocalGitSource({full: repo})).sync(full, None)
    login, uid = asker
    out = ask(uid, QUESTION)
    assert MARK_OUT in out["text"], "the stubbed model output reached the answer"

    finished = spans.get_finished_spans()
    assert finished
    forbidden = _forbidden(login, uid)
    for s in finished:
        assert not s.events, f"{s.name} has events"
        assert not s.status.description, f"{s.name} has a status message"
        for key, value in s.attributes.items():
            for bad in forbidden:
                assert bad not in str(value), f"{s.name}.{key} contains {bad!r}"


def test_an_http_question_leaves_no_query_or_login_in_its_spans(otel, asker):
    """FastAPI traces requests itself, URL query included; the exporter drops it."""
    from fastapi.testclient import TestClient

    from homegrown import api
    spans, _ = otel
    login, uid = asker
    r = TestClient(api.app).get("/ask", params={"q": QUESTION},
                                headers={"X-Homegrown-User": login})
    assert r.status_code == 200
    got = _by_name(spans.get_finished_spans())
    assert "answer.ask" in got and got["answer.ask"][0].attributes["homegrown.route"] == "http"
    for s in got.get("GET /ask", []):           # FastAPI's span, in versions that have it
        assert s.attributes["url.path"] == "/ask"
    forbidden = _forbidden(login, uid)
    for s in spans.get_finished_spans():
        assert "url.query" not in s.attributes
        for key, value in s.attributes.items():
            for bad in forbidden:
                assert bad not in str(value), f"{s.name}.{key} contains {bad!r}"


def test_llm_metrics_carry_labels_never_prompts(otel, asker, model_mode):
    from homegrown.answer import ask
    _, reader = otel
    login, uid = asker
    ask(uid, QUESTION)
    pts = _points(reader)
    calls = [a for a, _ in pts["homegrown.llm.calls"]]
    assert {"provider": "groq", "model": "test-model", "outcome": "ok"} in calls
    tokens = {a["type"] for a, _ in pts["homegrown.llm.tokens"] if a["model"] == "test-model"}
    assert tokens == {"input", "output"}
    assert {"route": "python", "kind": "ask", "answer": "yes"} in \
        [a for a, _ in pts["homegrown.questions"]]
    assert any(a.get("verdict") == "use_as_is" for a, _ in pts["homegrown.verdicts"])

    forbidden = _forbidden(login, uid)
    for name, points in pts.items():
        for attrs, _ in points:
            for value in attrs.values():
                for bad in forbidden:
                    assert bad not in str(value), f"{name} label contains {bad!r}"


def test_a_failed_call_records_the_error_type_not_its_message(otel, monkeypatch):
    import homegrown.llm as L
    spans, reader = otel
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "llm_model", "test-model-400")
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    # A provider error that echoes the prompt back: the message must go nowhere.
    monkeypatch.setattr(L.httpx, "post", lambda *a, **k: _Resp(400, text=f"bad: {QUESTION}"))
    with pytest.raises(L.LLMUnavailable):
        L.structured("system", QUESTION, {"type": "object", "properties": {}})
    call = _by_name(spans.get_finished_spans())["llm.call"][0]
    assert call.attributes["error.type"] == "LLMUnavailable"
    assert call.attributes["homegrown.llm.outcome"] == "unavailable"
    assert call.attributes["homegrown.llm.json_object_fallback"] is True
    assert not call.events and not call.status.description
    assert all(QUESTION not in str(v) for v in call.attributes.values())
    assert {"provider": "groq", "model": "test-model-400", "outcome": "unavailable"} in \
        [a for a, _ in _points(reader)["homegrown.llm.calls"]]


def test_completing_a_pass_records_how_long_the_change_waited(otel, mono):
    from homegrown.collector.state import complete, mark_dirty
    _, reader = otel
    full, _ = mono
    mark_dirty(full, T0)
    job = _claim_for(full, T0 + timedelta(seconds=600))
    assert complete(full, "w1", "sha1", job["started_at"], now=T0 + timedelta(seconds=700))
    hist = [p for _, p in _points(reader)["homegrown.sync.staleness"]]
    assert hist and hist[0].count >= 1 and hist[0].max >= 700


def test_repos_waiting_gauge_reads_the_queue(otel, mono):
    from homegrown.collector.state import mark_dirty, repos_waiting
    _, reader = otel
    full, _ = mono
    mark_dirty(full, T0)
    telemetry.watch_repos_waiting(repos_waiting)
    vals = [p.value for _, p in _points(reader)["homegrown.sync.repos_waiting"]]
    assert vals and vals[-1] >= 1
