"""Settings. Everything tunable lives here, nothing is hard-coded in the pipeline."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HOMEGROWN_", env_file=".env", extra="ignore")

    # Two roles. admin_dsn owns the schema. app_dsn is not a superuser and can't bypass
    # row-level security, so the policies in db/002_rls.sql apply to it. Serving
    # requests with admin_dsn would silently turn every policy off.
    admin_dsn: str = "postgresql://homegrown:homegrown_dev_only@localhost:5433/homegrown"
    app_dsn: str = "postgresql://homegrown_app:homegrown_app_dev_only@localhost:5433/homegrown"
    db_pool_size: int = 10              # connections per pool (admin, app)

    # "auto" uses CUDA when the installed onnxruntime can see it (.venv-gpu), else CPU.
    # "cpu" / "cuda" force it; "cuda" raises rather than silently falling back.
    device: str = "auto"

    embed_model: str = "BAAI/bge-base-en-v1.5"
    embed_dim: int = 768
    # Optional cross-encoder reranking. Off: it lowered recall in our evaluation
    # (experiments/gate1/RESULTS.md).
    rerank_model: str = "jinaai/jina-reranker-v1-tiny-en"
    rerank_enabled: bool = False
    rerank_depth: int = 30              # how many top results get reranked
    # The retrieval chosen in experiments/scale/RESULTS_gate1.md (addendum 15, "H-all"):
    # BM25 keywords + top-2 pooled vectors, fused by reciprocal rank. The older choices
    # stay available for comparison and rollback.
    # Keyword scoring: "bm25" (db/009_bm25.sql) or "ts_rank" (db/008_fts.sql).
    lexical: str = "bm25"
    # How an asset's several vectors score it: "top2" (mean of its two best) or "max".
    dense_pool: str = "top2"
    # How keyword and vector results are merged: "rrf" (sum of 1/(60+rank)),
    # "interleave" (alternate, vector first) or "max" (RRF using the better rank).
    fusion: str = "rrf"
    # How deep each list goes before fusion, so an asset that is only fair on one side
    # isn't cut off before the merge.
    fusion_depth: int = 200
    # The code's own words (db/011_code_words.sql) as a third keyword list, fused by RRF at
    # this weight. Only units the code reader has crawled have code words; 0 turns the list
    # off. Off since addendum 54 (RESULTS_gate1.md): on the goal benchmark G1 the list never
    # beat profile search (best option in the top 5: 56 with it, 58 without). The addendum-37
    # gain came from a set later found to be off-goal. Re-test when profiles come from code.
    code_words_weight: float = 0.0
    # "rules": verdict from the evidence alone, no model needed.
    # "model": an LLM judges each candidate against the request; fails safe to "unknown".
    verdict_mode: str = "rules"
    # Vector search: "exact" scans every visible vector; "hnsw" uses the index to fetch
    # a candidate pool and ranks that exactly; "auto" switches to hnsw once the table
    # has hnsw_auto_threshold vectors and the index exists. At 300k vectors: exact
    # ~1.5 s per search, hnsw ~0.1 s (experiments/scale/search_load_100k.txt).
    vector_search: str = "auto"
    hnsw_auto_threshold: int = 60_000
    hnsw_pool: int = 400
    hnsw_ef_search: int = 400

    # Model mode only: restate the request as a component's purpose line and search
    # both (one small model call per question).
    query_rewrite: bool = True
    # Model mode only: how many search results the judge reads per question.
    verdict_shortlist: int = 5

    # How many results the answering agent shows: every candidate judged a fit, then
    # (to fill up) candidates the judge could not rule out. Not-a-fit is never shown.
    answer_results: int = 5

    # Sync (collector/state.py, collector/sync.py). A changed repo is processed once it
    # has been quiet for sync_quiet_s, or waiting for sync_max_wait_s, whichever is first.
    sync_quiet_s: int = 120
    sync_max_wait_s: int = 480
    sync_lease_s: int = 900
    sync_poll_s: int = 300              # how often the poller lists changed repos

    # Owner approval. Off: new assets are published at `default_visibility` without
    # asking anyone. On: the collector pauses and asks the owner about visibility and
    # deprecation. Either way the agent never marks anything deprecated by itself.
    owner_gate: bool = False
    default_visibility: str = "org-wide"        # "org-wide" | "team-only"

    # Which LLM: "anthropic" | "gemini" | "groq" | "local" (any OpenAI-compatible server).
    # Free tiers may train on your prompts: never send private code to them.
    llm_provider: str = "anthropic"
    llm_model: str = ""                  # empty = auto (Gemini: newest stable flash)
    replay_file: str = ""                # llm_provider="replay": recorded outputs (development)

    # Code-reading crawler (bring your own model key). Off by default: without it, assets
    # come from manifests, READMEs and tests only. Areas are summarised by a cheap model and
    # units and profiles written by a stronger one.
    crawl_enabled: bool = False
    crawl_summary_model: str = "claude-haiku-5-5"
    crawl_compile_model: str = "claude-sonnet-5-5"
    crawl_max_files: int = 3000            # per repo; larger repos are crawled by area order
    crawl_area_vectors: int = 20           # area-summary vectors per unit
    # README characters the unit compiler reads; 0 = the whole README. An excerpt hid most
    # README claims in addendum 55 (RESULTS_gate1.md), and code is checked against all of them.
    crawl_readme_chars: int = 0

    # Answer traces (explainability): every answer stores why it came out that way, readable
    # by the person who asked. Off switch and retention (prune with explain.prune()).
    trace_enabled: bool = True
    trace_retention_days: int = 30
    llm_base_url: str = "http://localhost:11434/v1"    # for "local" (Ollama)
    gemini_key_file: str = ""
    groq_key_file: str = ""

    # Discussion filters. A length cut-off that works for one org can drop most of the
    # signal in another, so it is off unless you measure your own.
    min_comment_chars: int = 0          # 0 = keep comments of any length

    github_token_file: str = ""          # path to a file holding a GitHub token
    # Webhook secrets. Empty = every delivery is rejected (fail closed).
    github_webhook_secret: str = ""
    docs_webhook_secret: str = ""
    github_org: str = "grafana"

    # Traces and metrics over OpenTelemetry (docs/observability.md). Needs the [otel]
    # extra. The endpoint and service name come from the standard
    # OTEL_EXPORTER_OTLP_ENDPOINT and OTEL_SERVICE_NAME variables.
    otel_enabled: bool = False

    def github_token(self) -> str | None:
        """Read the token from a file. Never logged, never echoed."""
        import re
        from pathlib import Path

        if not self.github_token_file:
            return None
        p = Path(self.github_token_file)
        if not p.is_file():
            return None
        text = p.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"(ghp_|github_pat_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]+", text)
        return m.group(0) if m else text.strip() or None


settings = Settings()
