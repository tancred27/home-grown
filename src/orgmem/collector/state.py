"""Repo state, the scheduler and the per-repo lease (db/007_state_sync.sql).

Intake only calls `mark_dirty`. Workers loop on `claim_due` -> process -> `complete`
(or `fail`). Every function takes an optional `now` so a replay of real traffic can run
the real scheduling logic on a simulated clock.

When is a dirty repo due? (agreed rule "c")
    quiet for settings.sync_quiet_s since its newest change, OR
    its oldest pending change is older than settings.sync_max_wait_s.
So a busy repo that never goes quiet is still processed at least every max_wait, and
worst-case staleness is about max_wait + processing time.

Races
  * One worker per repo: claim_due takes a lease (lease_owner, lease_until) in the same
    statement that picks the repo, with FOR UPDATE SKIP LOCKED. An expired lease (a
    crashed worker) is claimable again.
  * A change arriving DURING a run moves last_seen_at past the run's start; complete()
    then leaves the repo dirty for the next pass. Nothing is lost.
  * Order of webhooks/polls is irrelevant: intake records only "something changed at
    time T"; the worker reads the current state itself.
"""

from __future__ import annotations

from datetime import datetime

from ..config import settings
from ..db import admin_conn


def mark_dirty(repo: str, seen_at: datetime | None = None,
               default_branch: str | None = None) -> None:
    with admin_conn() as conn:
        conn.execute(
            """insert into repo_state (repo, default_branch, dirty, first_pending_at,
                                       last_seen_at)
               values (%(r)s, %(b)s, true, coalesce(%(t)s, now()), coalesce(%(t)s, now()))
               on conflict (repo) do update set
                 dirty = true,
                 default_branch = coalesce(excluded.default_branch, repo_state.default_branch),
                 first_pending_at = case when repo_state.dirty
                     then least(repo_state.first_pending_at, excluded.first_pending_at)
                     else excluded.first_pending_at end,
                 last_seen_at = greatest(repo_state.last_seen_at, excluded.last_seen_at)""",
            {"r": repo, "b": default_branch, "t": seen_at})


def mark_dirty_many(rows: list[tuple[str, datetime | None]]) -> None:
    """Batch intake: one round trip for many (repo, seen_at)."""
    if not rows:
        return
    with admin_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """insert into repo_state (repo, dirty, first_pending_at, last_seen_at)
               values (%(r)s, true, coalesce(%(t)s, now()), coalesce(%(t)s, now()))
               on conflict (repo) do update set
                 dirty = true,
                 first_pending_at = case when repo_state.dirty
                     then least(repo_state.first_pending_at, excluded.first_pending_at)
                     else excluded.first_pending_at end,
                 last_seen_at = greatest(repo_state.last_seen_at, excluded.last_seen_at)""",
            [{"r": r, "t": t} for r, t in rows])


def claim_due(owner: str, now: datetime | None = None) -> dict | None:
    """Lease one due repo. -> {repo, last_processed_sha, default_branch, started_at}."""
    with admin_conn() as conn:
        row = conn.execute(
            """with due as (
                 select repo from repo_state
                  where dirty
                    and (lease_until is null or lease_until < coalesce(%(n)s, now()))
                    and (last_seen_at <= coalesce(%(n)s, now()) - make_interval(secs => %(q)s)
                         or first_pending_at <= coalesce(%(n)s, now())
                                                - make_interval(secs => %(w)s))
                  order by first_pending_at
                  for update skip locked
                  limit 1)
               update repo_state s
                  set lease_owner = %(o)s,
                      lease_until = coalesce(%(n)s, now()) + make_interval(secs => %(l)s)
                 from due where s.repo = due.repo
               returning s.repo, s.last_processed_sha, s.default_branch,
                         coalesce(%(n)s, now())""",
            {"n": now, "q": settings.sync_quiet_s, "w": settings.sync_max_wait_s,
             "l": settings.sync_lease_s, "o": owner}).fetchone()
    if not row:
        return None
    return {"repo": row[0], "last_processed_sha": row[1], "default_branch": row[2],
            "started_at": row[3]}


def complete(repo: str, owner: str, head_sha: str | None, started_at: datetime,
             head_ci: str | None = None, default_branch: str | None = None,
             now: datetime | None = None) -> bool:
    """Record a finished pass. Stays dirty if a change arrived after the pass started.
    Returns False if this worker no longer held the lease (its result is discarded)."""
    with admin_conn() as conn:
        row = conn.execute(
            """update repo_state set
                 last_processed_sha = coalesce(%(h)s, last_processed_sha),
                 last_processed_at = coalesce(%(n)s, now()),
                 head_ci = coalesce(%(c)s, head_ci),
                 default_branch = coalesce(%(b)s, default_branch),
                 dirty = last_seen_at > %(s)s,
                 first_pending_at = case when last_seen_at > %(s)s then %(s)s end,
                 lease_owner = null, lease_until = null,
                 failures = 0, last_error = null
               where repo = %(r)s and lease_owner = %(o)s
               returning repo""",
            {"h": head_sha, "n": now, "c": head_ci, "b": default_branch, "s": started_at,
             "r": repo, "o": owner}).fetchone()
    return row is not None


def fail(repo: str, owner: str, error: str, now: datetime | None = None) -> None:
    """Release with exponential backoff (1, 2, 4 … max 60 minutes); stays dirty."""
    with admin_conn() as conn:
        conn.execute(
            """update repo_state set
                 failures = failures + 1, last_error = %(e)s, lease_owner = null,
                 lease_until = coalesce(%(n)s, now())
                     + make_interval(mins => least(60, power(2, failures)::int))
               where repo = %(r)s and lease_owner = %(o)s""",
            {"e": error[:2000], "n": now, "r": repo, "o": owner})


def claim_daily(owner: str, now: datetime | None = None) -> dict | None:
    """Lease one repo whose daily refresh (contributors, stage) is due. Shares the same
    lease as normal passes, so a daily refresh never overlaps a sync of the same repo."""
    with admin_conn() as conn:
        row = conn.execute(
            """with due as (
                 select repo from repo_state
                  where last_processed_sha is not null
                    and coalesce(daily_due_at, '-infinity') <= coalesce(%(n)s, now())
                    and (lease_until is null or lease_until < coalesce(%(n)s, now()))
                  order by daily_due_at nulls first
                  for update skip locked
                  limit 1)
               update repo_state s
                  set lease_owner = %(o)s,
                      lease_until = coalesce(%(n)s, now()) + make_interval(secs => %(l)s),
                      daily_due_at = coalesce(%(n)s, now()) + interval '1 day'
                 from due where s.repo = due.repo
               returning s.repo, s.last_processed_sha, coalesce(%(n)s, now())""",
            {"n": now, "o": owner, "l": settings.sync_lease_s}).fetchone()
    if not row:
        return None
    return {"repo": row[0], "last_processed_sha": row[1], "started_at": row[2]}


def release_lease(repo: str, owner: str) -> None:
    with admin_conn() as conn:
        conn.execute("update repo_state set lease_owner = null, lease_until = null "
                     "where repo = %s and lease_owner = %s", (repo, owner))
