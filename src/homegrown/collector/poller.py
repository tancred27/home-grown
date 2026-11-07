"""Intake by polling (the default; webhooks are an optional accelerator).

Every settings.sync_poll_s seconds, per org: list repos sorted by most recent push,
newest first, 100 per call, and stop at the first repo pushed before the previous poll.
Each repo pushed since then is marked dirty. In a 5-minute window even a busy org
usually needs one or two calls. The worker later reads each repo's default-branch head
and does nothing if it hasn't moved (a push to a feature branch also bumps pushed_at).

Why polling first: no public endpoint (works behind enterprise firewalls and for small
teams), and self-healing (a missed webhook can't lose an update; every poll re-reads
the truth).
"""

from __future__ import annotations

from datetime import UTC, datetime

from .. import telemetry
from .state import mark_dirty_many


def _ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


@telemetry.traced("sync.poll")
def poll_org(org: str, since: datetime | None, get=None, max_pages: int = 100) -> dict:
    """Mark every repo of `org` pushed after `since` dirty. -> {calls, changed, until}.
    `since=None` (first run) marks every repo dirty: the initial backfill."""
    if get is None:
        from .adapters import _gh

        def get(url, headers=None):
            with _gh() as c:
                r = c.get(url, headers=headers or {})
            return r.status_code, r.json()
    started = datetime.now(UTC)
    changed: list[tuple[str, datetime | None]] = []
    calls = 0
    for page in range(1, max_pages + 1):
        st, batch = get(f"https://api.github.com/orgs/{org}/repos?type=all&sort=pushed"
                        f"&direction=desc&per_page=100&page={page}", None)
        calls += 1
        if st != 200 or not isinstance(batch, list) or not batch:
            break
        stop = False
        for r in batch:
            pushed = _ts(r.get("pushed_at"))
            if since is not None and pushed is not None and pushed <= since:
                stop = True
                break
            if not r.get("archived"):
                changed.append((r["full_name"], pushed))
        if stop or len(batch) < 100:
            break
    mark_dirty_many(changed)
    telemetry.annotate({"homegrown.org": org, "homegrown.calls": calls,
                        "homegrown.changed": len(changed)})
    return {"org": org, "calls": calls, "changed": len(changed), "until": started}
