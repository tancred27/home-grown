"""Traces and metrics, over OpenTelemetry. Off unless HOMEGROWN_OTEL_ENABLED=true.

Off, every span and instrument below is the OpenTelemetry API's no-op and the SDK is
never imported. On, `init()` exports both over OTLP/HTTP to OTEL_EXPORTER_OTLP_ENDPOINT
(default http://localhost:4318).

Never record content. No question text, prompts, model output, claim bodies, READMEs,
logins or emails: only ids, counts, durations, model names, labels and error types.
What each span and metric carries is listed in docs/observability.md.
"""

from __future__ import annotations

import functools
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, StatusCode

from .config import settings

tracer = trace.get_tracer("homegrown")
meter = metrics.get_meter("homegrown")

_enabled = False


def enabled() -> bool:
    return _enabled


def init(component: str, span_exporter=None, metric_reader=None) -> bool:
    """Start exporting, if HOMEGROWN_OTEL_ENABLED. Call once at process start; a second
    call does nothing. Tests pass an in-memory exporter and reader instead of OTLP."""
    global _enabled
    if _enabled or not settings.otel_enabled:
        return _enabled
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    resource = Resource.create({
        "service.name": os.environ.get("OTEL_SERVICE_NAME") or f"homegrown-{component}",
        # One series per process, so two workers' counters never overwrite each other.
        "service.instance.id": uuid.uuid4().hex,
    })
    if span_exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        processor = BatchSpanProcessor(_scrubbing(OTLPSpanExporter()))
    else:
        processor = SimpleSpanProcessor(_scrubbing(span_exporter))
    if metric_reader is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(resource=resource,
                                             metric_readers=[metric_reader]))
    _enabled = True
    return True


def _scrubbing(exporter):
    """Wrap an exporter so no span leaves with exception events or a status message.
    Ours never set them, but libraries (the MCP SDK) do, and an error message can quote
    a request or a model's output."""
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter
    from opentelemetry.trace import Status

    class Scrubbing(SpanExporter):
        def export(self, spans):
            return exporter.export([ReadableSpan(
                name=s.name, context=s.context, parent=s.parent, resource=s.resource,
                attributes=s.attributes, links=s.links, kind=s.kind,
                status=Status(s.status.status_code), start_time=s.start_time,
                end_time=s.end_time, instrumentation_scope=s.instrumentation_scope)
                for s in spans])

        def shutdown(self):
            exporter.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return exporter.force_flush(timeout_millis)

    return Scrubbing()


# ---------------------------------------------------------------- spans

@contextmanager
def span(name: str, attrs: dict | None = None) -> Iterator[Span]:
    """A span that records an error's type and never its message."""
    with tracer.start_as_current_span(name, attributes=attrs, record_exception=False,
                                      set_status_on_exception=False) as s:
        try:
            yield s
        except Exception as e:
            from langgraph.errors import GraphBubbleUp
            if not isinstance(e, GraphBubbleUp):     # interrupt() is a pause, not an error
                s.set_attribute("error.type", type(e).__name__)
                s.set_status(StatusCode.ERROR)
            raise


def traced(name: str) -> Callable:
    """Decorator: run the function inside span(name)."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            with span(name):
                return fn(*args, **kwargs)
        return inner
    return wrap


def annotate(attrs: dict) -> None:
    """Add attributes to the current span (a no-op when there is none)."""
    trace.get_current_span().set_attributes(attrs)


# ---------------------------------------------------------------- metrics

QUESTIONS = meter.create_counter(
    "homegrown.questions", unit="{question}",
    description="Questions answered, by route (cli, mcp, http), kind and answer word")
QUESTION_DURATION = meter.create_histogram(
    "homegrown.question.duration", unit="s", description="Time to answer a question",
    explicit_bucket_boundaries_advisory=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60])
VERDICTS = meter.create_counter(
    "homegrown.verdicts", unit="{verdict}", description="Verdicts returned, by label")
LLM_CALLS = meter.create_counter(
    "homegrown.llm.calls", unit="{call}",
    description="Model calls, by provider, model and outcome")
LLM_TOKENS = meter.create_counter(
    "homegrown.llm.tokens", unit="{token}",
    description="Tokens the provider reported, by model and type (input, output)")
SYNC_PASSES = meter.create_counter(
    "homegrown.sync.passes", unit="{pass}", description="Sync passes, by status")
SYNC_PASS_DURATION = meter.create_histogram(
    "homegrown.sync.pass.duration", unit="s", description="Time for one sync pass",
    explicit_bucket_boundaries_advisory=[0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800])
SYNC_STALENESS = meter.create_histogram(
    "homegrown.sync.staleness", unit="s",
    description="From a repo's oldest pending change to the end of the pass that took it",
    explicit_bucket_boundaries_advisory=[30, 60, 120, 240, 480, 600, 900, 1800, 3600,
                                         7200, 21600])


def record_question(route: str, kind: str, seconds: float, answer: str | None = None):
    attrs = {"route": route, "kind": kind}
    if answer:
        attrs["answer"] = answer
    QUESTIONS.add(1, attrs)
    QUESTION_DURATION.record(seconds, {"route": route, "kind": kind})


def watch_repos_waiting(count: Callable[[], int]) -> None:
    """Report count() (repos marked changed, not yet processed) on every export."""
    def observe(_options):
        try:
            return [metrics.Observation(count())]
        except Exception:
            return []                  # a database blip must not break the export
    meter.create_observable_gauge(
        "homegrown.sync.repos_waiting", callbacks=[observe], unit="{repo}",
        description="Repos marked changed and waiting for a sync pass")
