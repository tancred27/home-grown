"""Settings. Everything tunable lives here, nothing is hard-coded in the pipeline."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORGMEM_", env_file=".env", extra="ignore")

    # Two roles on purpose. admin_dsn owns the schema; app_dsn is NOT a superuser and
    # is NOBYPASSRLS, so row-level security actually applies to it. Using admin_dsn at
    # runtime would silently disable every policy in db/002_rls.sql.
    admin_dsn: str = "postgresql://orgmem:orgmem_dev_only@localhost:5433/orgmem"
    app_dsn: str = "postgresql://orgmem_app:orgmem_app_dev_only@localhost:5433/orgmem"

    # "auto" uses CUDA when the installed onnxruntime can see it (.venv-gpu), else CPU.
    # "cpu" / "cuda" force it; "cuda" raises rather than silently falling back.
    device: str = "auto"

    embed_model: str = "BAAI/bge-base-en-v1.5"
    embed_dim: int = 768
    # Small cross-encoder (~130MB, onnxruntime). NOT the Xenova ms-marco exports: in
    # this fastembed build both the L-6 and L-12 variants returned scores that rose
    # monotonically with document POSITION regardless of content (-11.44, -11.40,
    # -11.32 on a trivial case), ranking the correct answer last.
    rerank_model: str = "jinaai/jina-reranker-v1-tiny-en"
    # OFF by default: measured to hurt. On the held-out set, hybrid recall@5 was 0.73
    # without reranking and 0.62 with it. See experiments/gate1/RESULTS.md.
    rerank_enabled: bool = False
    rerank_depth: int = 30          # how many fused candidates get cross-encoded
    # How lexical and semantic candidates are combined: "max" (max-fusion with a
    # both-signals tie-break) or "interleave" (ties broken toward semantic).
    # Selected on the tuning set and confirmed on the untouched test set (recall@5 0.82,
    # recall@1 0.59); see experiments/gate1/RESULTS.md run 3.
    fusion: str = "interleave"
    # "rules": the original rule-based verdict, which never looks at relevance.
    # "model": an LLM judges each candidate against the request and fails safe to
    # "unknown". See experiments/gate2/RESULTS.md.
    verdict_mode: str = "rules"

    # Discussion ingestion is a funnel. These are the gates, and they are settings
    # rather than constants because run E2 showed the length gate does NOT generalise:
    # comments >=500 chars held 79% of signal in popular repos but only 37% in obscure
    # ones. Measure per corpus before trusting a value. See experiments/density.
    min_comment_chars: int = 0          # 0 = no gate; raise only with measurement
    drop_bot_comments: bool = True

    # Retrieval
    search_candidates: int = 50          # pulled from each of FTS and vector
    verdict_candidates: int = 5          # how many reach the verdict step
    rerank_floor: float = 0.0            # below this, return nothing

    github_token_file: str = ""          # path to a file holding a GitHub token
    # Webhook secrets. Empty = every delivery is rejected (fail closed).
    github_webhook_secret: str = ""
    docs_webhook_secret: str = ""
    github_org: str = "grafana"

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
