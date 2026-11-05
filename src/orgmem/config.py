"""Settings. Everything tunable lives here, nothing is hard-coded in the pipeline."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORGMEM_", env_file=".env", extra="ignore")

    # Two roles on purpose. admin_dsn owns the schema; app_dsn is NOT a superuser and
    # is NOBYPASSRLS, so row-level security actually applies to it. Using admin_dsn at
    # runtime would silently disable every policy in db/002_rls.sql.
    admin_dsn: str = "postgresql://orgmem:orgmem_dev_only@localhost:5433/orgmem"
    app_dsn: str = "postgresql://orgmem_app:orgmem_app_dev_only@localhost:5433/orgmem"

    embed_model: str = "BAAI/bge-base-en-v1.5"
    embed_dim: int = 768

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
