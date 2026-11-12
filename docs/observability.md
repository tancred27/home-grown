# Observability

HomeGrown can send traces and metrics over [OpenTelemetry](https://opentelemetry.io)
to any OTLP backend. It is off by default. When it's off, every span and metric is the
OpenTelemetry API's no-op, and the SDK isn't loaded.

The design rule is simple: **telemetry never holds content.** It records ids, counts,
durations, model names, labels and error types. It doesn't record what people asked or
what the code says.

## Turn it on

```bash
pip install -e ".[otel]"
docker compose --profile observability up -d      # collector, Tempo, Prometheus, Grafana

export HOMEGROWN_OTEL_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # the default
homegrown ask "has anyone built a way to escape HTML safely?"
```

Every entry point starts telemetry: the CLI, the API, the MCP server, and both workers.
Each process reports as its own service: `homegrown-api`, `homegrown-cli`,
`homegrown-mcp`, `homegrown-sync-worker` and `homegrown-collector-worker`. Set
`OTEL_SERVICE_NAME` to override it. Data goes out over OTLP/HTTP, so you can point
`OTEL_EXPORTER_OTLP_ENDPOINT` at any collector you already run. The other standard
`OTEL_*` variables also work, for example `OTEL_METRIC_EXPORT_INTERVAL` (milliseconds,
default 60000).

`docker compose up -d` without the profile still starts only the database.

## Open Grafana

Go to <http://localhost:3000>. The **HomeGrown** dashboard is the home page. It shows:
- questions per minute and answer latency (p50, p95);
- the verdict mix;
- model calls, tokens and failures by model;
- sync passes, pass duration, repos waiting and staleness (p95).

You can view it without signing in. To edit, sign in as `admin` / `admin`, and Grafana
will ask you to pick a new password. To look at traces, use Explore with the Tempo data
source, for example `{resource.service.name="homegrown-api"}`.

All four services listen on 127.0.0.1 only:

| Service | Port |
|---|---|
| Grafana | 3000 |
| Prometheus | 9090 |
| Tempo | 3200 |
| OTLP collector | 4318 (HTTP), 4317 (gRPC) |

The setup is for one machine. For a shared deployment, put Grafana behind your sign-in
and turn off anonymous viewing (`GF_AUTH_ANONYMOUS_ENABLED`). Configs live in
`deploy/observability/`.

## What is recorded

**Spans**

| Span | Attributes |
|---|---|
| `answer.ask` | route (`cli`, `mcp`, `http`), answer word, number of matches |
| `answer.retrieve_and_judge`, `answer.enrich`, `answer.compose` | number of candidates and matches |
| `search.find_existing` | question length, number of keywords, verdict mode, number of results |
| `search.rewrite_query` | outcome: `skipped`, `cached`, `rewritten`, `none` |
| `search` | lexical, semantic and fused result counts, fusion, rerank on or off |
| `search.embed`, `search.lexical`, `search.semantic` | result count; vector mode (`exact` or `hnsw`) |
| `search.judge_with_model` | candidates, how many were judged, outcome, error type |
| `search.judge_smart` | candidates, picks returned, outcome, error type |
| `search.judge_requirements` | candidates, requirements found, how many were judged, outcome, error type |
| `llm.call` | provider, model, max tokens, input and output tokens, retries, latency, outcome (`ok`, `refusal`, `quota`, `unavailable`, `error`) |
| `sync.poll` | org, API calls, repos changed |
| `sync.claim` | whether a repo was claimed, and which |
| `sync.pass` | repo, daily or not, status, units, changed units, files changed |
| `collect.<node>` | one per collector node: number of targets and claims, decision |

The FastAPI and MCP SDKs add their own request spans: method, route, status code and
tool name.

Errors are recorded by type (`error.type`, such as `LLMUnavailable`), never by message.

**Metrics**

| Metric | Labels |
|---|---|
| `homegrown.questions` | route, kind (`ask`, `find`), answer |
| `homegrown.question.duration` (seconds) | route, kind |
| `homegrown.verdicts` | verdict, mode |
| `homegrown.llm.calls` | provider, model, outcome |
| `homegrown.llm.tokens` | model, type (`input`, `output`) |
| `homegrown.sync.passes` | status, daily |
| `homegrown.sync.pass.duration` (seconds) | status |
| `homegrown.sync.repos_waiting` | none; reported by the sync worker |
| `homegrown.sync.staleness` (seconds) | none: from a repo's oldest pending change to the end of the pass that took it |

## What is never recorded

- question text, keywords or request URLs (only the question's length);
- prompts or model output, including the judge's reasons;
- claim bodies, READMEs, docs pages or commit messages;
- user logins, emails or user ids;
- error messages and stack traces.

The exporter enforces part of this for libraries too. Before a span leaves the process,
it drops URL query attributes (FastAPI's span for `GET /ask?q=...` would otherwise carry
the question), exception events and status messages. When telemetry is on, the
Anthropic SDK's own spans are switched off, because they can be set to record content.
`tests/test_telemetry.py` asks real questions with a stubbed model and fails if the
question, the model output, a prompt, a claim or the user's identity turns up in any
span or metric.

Third-party settings can still add content: LangSmith tracing (`LANGSMITH_TRACING`),
`FASTAPI_OTEL_AUTO_CONFIGURE`, or the `ANTHROPIC_OPEN_TELEMETRY_*` variables in a process
that has HomeGrown's telemetry off. Leave them unset.

## Retention

Tempo keeps traces for 30 days (`block_retention: 720h` in
`deploy/observability/tempo.yaml`). Prometheus keeps metrics for 30 days
(`--storage.tsdb.retention.time=30d` in `docker-compose.yml`). The collector and
Grafana store no telemetry. To delete everything now, remove the containers and their
volumes:

```bash
docker compose --profile observability rm -sf otel-collector tempo prometheus grafana
docker volume rm <project>_homegrown_tempo <project>_homegrown_prometheus
```

`<project>` is the compose project name, usually the folder name. Don't run
`docker compose down -v`: it would delete the database volume too.

## Limits

- A one-off CLI command is its own short-lived series in Prometheus, so `rate()`
  undercounts it. Questions through the API and the MCP server, which run for a long
  time, are counted properly.
- With telemetry on and the collector down, a CLI command can take a few seconds
  longer to exit while the exporter gives up.
