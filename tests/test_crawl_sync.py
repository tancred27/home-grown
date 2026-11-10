"""The crawler inside a real sync pass: a throwaway git repo, models stubbed."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from homegrown import llm
from homegrown.collector import crawl
from homegrown.collector.source import LocalGitSource
from homegrown.collector.sync import Syncer
from homegrown.config import settings
from homegrown.db import admin_conn


def git(repo: Path, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", "-C", str(repo), *args],
                   check=True, capture_output=True)


def write(repo: Path, rel: str, text: str):
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def _decls(prefix: str, n: int = 30) -> str:
    return "\n".join(f"export function {prefix}{i}(x: number): number {{ return x }}"
                     for i in range(n))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    name = f"zz-crawl-{uuid.uuid4().hex[:6]}"
    r = tmp_path / name
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, "README.md", f"# {name}\n\nRate limiting and retries.\n")
    write(r, "package.json", json.dumps({"name": name, "description": "Rate limiting"}))
    write(r, "src/limiter/core/bucket.ts", _decls("takeToken"))
    write(r, "src/retry/policy/backoff.ts", _decls("backoffDelay"))
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    full = f"testorg/{name}"

    def fake(system, user, schema, max_tokens=2000, model=None):
        if system == crawl.AREA_SYS:
            return {"summary": f"Area summary ({len(user)} chars)."}
        if system == crawl.UNITS_SYS:
            areas = [a["area"] for a in json.loads(user)["code_areas"]]
            return {"units": [{"name": "Token bucket limiter", "unit_type": "library",
                               "purpose": "Limit request rates.", "capabilities": ["token bucket"],
                               "search_terms": ["rate limit"], "example_questions": [],
                               "distinct": "", "areas": areas}]}
        raise llm.LLMUnavailable("not stubbed")
    monkeypatch.setattr(llm, "structured", fake)
    monkeypatch.setattr(settings, "crawl_enabled", True)
    yield full, r, name
    with admin_conn() as conn:
        root = conn.execute("select id from asset where unit_id = %s",
                            (f"github:{name}:.",)).fetchone()
        if root:
            conn.execute("delete from asset where parent_id = %s", (root[0],))
        conn.execute("delete from area where repo = %s", (full,))
        conn.execute("delete from asset where unit_id like %s", (f"github:{name}:%",))
        conn.execute("delete from unit_fingerprint where repo = %s", (full,))


def test_sync_crawls_code_and_a_source_only_change_reaches_the_crawler(repo):
    full, r, name = repo
    s = Syncer(LocalGitSource({full: r}))
    first = s.sync(full, None)
    assert first["crawl"]["status"] == "crawled", first["crawl"]
    with admin_conn() as conn:
        units = conn.execute(
            "select a.name, a.kind from asset a join asset p on p.id = a.parent_id "
            "where p.unit_id = %s", (f"github:{name}:.",)).fetchall()
    assert units == [("Token bucket limiter", "library")]

    write(r, "src/retry/policy/backoff.ts", _decls("jitter", 5) + "\n" + _decls("backoffDelay"))
    git(r, "commit", "-qam", "source only")
    second = s.sync(full, first["head"])
    assert second["status"] == "fingerprints-only"            # nothing the old path reads
    assert second["crawl"]["status"] == "crawled"             # but the code changed
    assert second["crawl"]["summarised"] == 1                 # only the changed area


def test_crawl_off_by_default_changes_nothing(repo, monkeypatch):
    full, r, _ = repo
    monkeypatch.setattr(settings, "crawl_enabled", False)
    out = Syncer(LocalGitSource({full: r})).sync(full, None)
    assert "crawl" not in out
