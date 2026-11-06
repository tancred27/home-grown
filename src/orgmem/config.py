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
    # Restate the request as a component purpose line before searching (one small LLM
    # call per question; only in "model" mode, fails safe to plain search). Biggest
    # measured gain at scale: test recall@5 0.69 -> 0.82 at ~1,900 assets.
    query_rewrite: bool = True
    # How many search results the model judge reads per question (only in "model" mode).
    # 5, not 15: on all 30 gate-2 duplicate tickets, at 109, 515 and 1,932 assets, top 15
    # held exactly as many gold assets as top 5; the misses sit at ranks 17-54. Depth 15
    # cost ~3x the tokens per question for nothing (experiments/gate2/RESULTS.md).
    verdict_shortlist: int = 5

    # How many results the answering agent shows: every candidate judged a fit, then
    # (to fill up) candidates the judge could not rule out. Not-a-fit is never shown.
    answer_results: int = 5

    # Owner approval in the collection agent. OFF by default: new assets become
    # discoverable at `default_visibility` with nobody asked, and nothing waits for a person.
    # ON restores the interrupt() that asks the owner about visibility and deprecation.
    # Either way the agent never applies a deprecation by itself (CLAUDE.md): with the
    # gate off, a deprecation signal is recorded as an unconfirmed note in the stage basis.
    owner_gate: bool = False
    default_visibility: str = "org-wide"        # "org-wide" | "team-only"

    # Which LLM. "anthropic" | "gemini" | "local" (any OpenAI-compatible server).
    # The Gemini FREE tier may use prompts to improve Google's products: acceptable for
    # this project's public repos, never for private code.
    llm_provider: str = "anthropic"
    llm_model: str = ""                  # empty = auto (Gemini: newest stable flash)
    llm_base_url: str = "http://localhost:11434/v1"    # for "local" (Ollama)
    gemini_key_file: str = ""
    groq_key_file: str = ""

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
