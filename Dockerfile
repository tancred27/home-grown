# HomeGrown: the API by default. Run a worker by overriding the command:
#   docker run ... ghcr.io/tancred27/home-grown python -I scripts/sync_worker.py <org>
# Settings come from HOMEGROWN_* environment variables (docs/configuration.md).

FROM python:3.12-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir build==1.2.2.post1
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m build --wheel --outdir /wheels


FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/home/homegrown/.cache/fastembed
# git: the sync worker's local-mirror mode reads repos with it.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 homegrown
COPY --from=build /wheels /wheels
# [otel] so telemetry can be turned on; it stays off unless HOMEGROWN_OTEL_ENABLED=true.
# The sync cache sits three folders above the installed package, so make it writable.
RUN pip install --no-cache-dir "$(ls /wheels/*.whl)[otel]" && rm -rf /wheels \
    && mkdir /usr/local/lib/.cache && chown homegrown /usr/local/lib/.cache
WORKDIR /app
COPY scripts/sync_worker.py scripts/collector_worker.py ./scripts/
USER homegrown
EXPOSE 8080
CMD ["uvicorn", "homegrown.api:app", "--host", "0.0.0.0", "--port", "8080"]
