"""Source adapters: each turns its own payload into the common Event envelope.

  github          push webhook payloads, and the REST API for replay / reconcile
  confluence      page_created / page_updated webhook payloads
  notion          page updated webhook payloads
  docs-markdown   markdown pages in a repo's docs/ folder (the public stand-in for a
                  wiki: real documentation, real authors, no account needed)

The Confluence and Notion mappers follow the documented webhook event shapes in outline
(page id, version, title, author, body); verify field names against the live APIs before
production. Everything downstream depends only on Event, so adding a source means adding
one function here.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

import httpx

from ..config import settings
from .events import ChangedFile, Event

API = "https://api.github.com"


# ---------------------------------------------------------------- GitHub

def verify_github_signature(secret: str, body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256: sha256=<hex HMAC of the raw body>. Constant-time compare."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, header.removeprefix("sha256="))


def from_github_push(payload: dict) -> list[Event]:
    """One push webhook -> one event per commit in it."""
    repo = payload.get("repository", {}).get("full_name", "")
    out = []
    for c in payload.get("commits", []):
        files = ([ChangedFile(path=p, status="added") for p in c.get("added", [])]
                 + [ChangedFile(path=p, status="modified") for p in c.get("modified", [])]
                 + [ChangedFile(path=p, status="removed") for p in c.get("removed", [])])
        msg = c.get("message", "")
        out.append(Event(
            source="github", kind="commit", object_ref=repo, version=c["id"],
            actor=(c.get("author") or {}).get("username"),
            occurred_at=c.get("timestamp"), title=msg.splitlines()[0] if msg else "",
            body=msg, url=c.get("url"), files=files))
    return out


def _gh() -> httpx.Client:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "org-memory/0.1"}
    tok = settings.github_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return httpx.Client(headers=h, timeout=30.0, follow_redirects=True)


def github_recent_commits(full_name: str, limit: int = 5,
                          with_files: bool = True) -> list[Event]:
    """Replay / reconcile path: the same Event a webhook would have produced.

    GitHub does not redeliver failed webhooks, so a reconcile job that re-reads recent
    commits is mandatory; the idempotency key makes the overlap harmless.
    """
    out = []
    with _gh() as cl:
        r = cl.get(f"{API}/repos/{full_name}/commits", params={"per_page": limit})
        r.raise_for_status()
        for c in r.json()[:limit]:
            files = []
            if with_files:
                d = cl.get(f"{API}/repos/{full_name}/commits/{c['sha']}")
                if d.status_code == 200:
                    files = [ChangedFile(path=f["filename"], status=f.get("status", "modified"),
                                         additions=f.get("additions", 0),
                                         deletions=f.get("deletions", 0))
                             for f in d.json().get("files", [])[:300]]
            msg = c["commit"]["message"]
            out.append(Event(
                source="github", kind="commit", object_ref=full_name, version=c["sha"],
                actor=(c.get("author") or {}).get("login")
                or c["commit"]["author"].get("name"),
                occurred_at=c["commit"]["author"].get("date"),
                title=msg.splitlines()[0], body=msg, url=c.get("html_url"), files=files))
    return out


def github_contributors(full_name: str, path: str | None = None,
                        limit: int = 100) -> list[dict]:
    """[{login, commits, first_at, last_at, email?}] from recent history, most active first."""
    params = {"per_page": limit}
    if path and path != ".":
        params["path"] = path
    with _gh() as cl:
        r = cl.get(f"{API}/repos/{full_name}/commits", params=params)
        if r.status_code != 200:
            return []
        agg: dict[str, dict] = {}
        for c in r.json():
            login = (c.get("author") or {}).get("login")
            if not login or login.endswith("[bot]") or login.endswith("-bot"):
                continue
            d = c["commit"]["author"].get("date")
            a = agg.setdefault(login, {"login": login, "commits": 0,
                                       "first_at": d, "last_at": d})
            email = c["commit"]["author"].get("email")
            if email and not a.get("email"):
                a["email"] = email          # hashed by directory.resolve, never stored
            a["commits"] += 1
            a["first_at"] = min(a["first_at"], d)
            a["last_at"] = max(a["last_at"], d)
    return sorted(agg.values(), key=lambda a: -a["commits"])


def github_releases(full_name: str, limit: int = 10) -> list[dict]:
    with _gh() as cl:
        r = cl.get(f"{API}/repos/{full_name}/releases", params={"per_page": limit})
        if r.status_code != 200:
            return []
        return [{"tag": x.get("tag_name", ""), "prerelease": bool(x.get("prerelease")),
                 "published_at": x.get("published_at")} for x in r.json()
                if not x.get("draft")]


# ---------------------------------------------------------------- documentation pages

def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def from_confluence_webhook(payload: dict) -> Event:
    page = payload.get("page", {})
    kind = "page_created" if payload.get("webhookEvent", "").endswith("created") \
        else "page_updated"
    space = page.get("spaceKey") or (page.get("space") or {}).get("key", "")
    return Event(
        source="confluence", kind=kind,
        object_ref=f"confluence:{space}/{page.get('id')}",
        version=str((page.get("version") or {}).get("number", page.get("version", 0))),
        actor=(payload.get("user") or {}).get("accountId")
        or page.get("creatorAccountId"),
        occurred_at=str(payload.get("timestamp", "")), title=page.get("title"),
        body=_strip_html((page.get("body") or {}).get("storage", {}).get("value", "")
                         if isinstance(page.get("body"), dict) else page.get("body", "")),
        url=page.get("self") or page.get("url"))


def from_notion_webhook(payload: dict) -> Event:
    ent = payload.get("entity", {}) or payload.get("page", {})
    return Event(
        source="notion", kind="page_updated",
        object_ref=f"notion:{ent.get('id')}",
        version=str(payload.get("timestamp") or ent.get("last_edited_time", "")),
        actor=(payload.get("authors") or [{}])[0].get("id"),
        occurred_at=str(payload.get("timestamp", "")),
        title=ent.get("title"), body=ent.get("text", ""), url=ent.get("url"))


def docs_markdown_pages(repo_root: Path, full_name: str, author: str | None = None,
                        limit: int = 20) -> list[Event]:
    """Markdown under docs/ as documentation-page events (version = content hash)."""
    out = []
    for p in sorted(repo_root.glob("docs/**/*.md"))[:limit]:
        text = p.read_text(encoding="utf-8", errors="ignore")
        rel = p.relative_to(repo_root).as_posix()
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines()
                      if ln.startswith("#")), p.stem)
        out.append(Event(
            source="docs-markdown", kind="page_updated",
            object_ref=f"docs:{full_name}/{rel}",
            version=hashlib.sha256(text.encode()).hexdigest()[:16],
            actor=author, title=title, body=text[:20000],
            url=f"https://github.com/{full_name}/blob/HEAD/{rel}"))
    return out
