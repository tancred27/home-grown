"""Fetch source-system metadata. GitHub for now, behind a thin interface.

Only the repository's own `description` is used, and it matters more than it sounds:
gate 1 showed that several assets were unfindable purely because their extracted
summary was useless ("carbon-relay-ng", "> [!NOTE]"). A human-written one-liner from
the source system beats anything scraped out of a README.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from ..config import settings

API = "https://api.github.com"
UA = "org-memory/0.1"


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    tok = settings.github_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def fetch_org_repos(org: str | None = None, cache: Path | None = None,
                    refresh: bool = False) -> dict[str, dict]:
    """{repo_name: {description, language, stars, pushed_at, archived}}.

    Cached to disk, because this is metadata that changes slowly and we would rather
    not spend rate limit on every ingest.
    """
    org = org or settings.github_org
    cache = cache or Path(".cache") / f"{org}_repos.json"
    if cache.is_file() and not refresh:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    out: dict[str, dict] = {}
    with httpx.Client(headers=_headers(), timeout=30.0, follow_redirects=True) as cl:
        for page in range(1, 11):
            r = cl.get(f"{API}/orgs/{org}/repos",
                       params={"per_page": 100, "page": page, "type": "public"})
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for repo in batch:
                out[repo["name"]] = {
                    "description": repo.get("description"),
                    "language": repo.get("language"),
                    "stars": repo.get("stargazers_count", 0),
                    "pushed_at": repo.get("pushed_at"),
                    "archived": repo.get("archived", False),
                }
            if len(batch) < 100:
                break
            time.sleep(0.2)

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out
