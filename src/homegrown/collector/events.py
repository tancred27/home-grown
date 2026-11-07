"""The common event envelope every source adapter produces, and the Postgres queue.

Every adapter (GitHub, Confluence, Notion, markdown docs)
turns its own payload into one shape. The idempotency key is source + object + version,
so a redelivered webhook, a reconcile job that re-reads the same commit, or an
out-of-order Confluence event is harmless: the second insert is a no-op.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from ..db import admin_conn

# "reconcile" is the backfill / nightly-reconcile event: "re-read every unit in this
# repo". It goes through the same graph as a live commit (backfill is not a separate
# system), and it is how missed changes get caught up.
Kind = Literal["commit", "page_created", "page_updated", "release", "reconcile"]


class ChangedFile(BaseModel):
    path: str
    status: str = "modified"          # added | modified | removed | renamed
    additions: int = 0
    deletions: int = 0


class Event(BaseModel):
    source: str                        # github | confluence | notion | docs-markdown
    kind: Kind
    object_ref: str                    # "grafana/loki" or "confluence:ENG/12345"
    version: str                       # commit sha or page version
    actor: str | None = None           # source login of whoever made the change
    occurred_at: str | None = None
    title: str | None = None           # commit subject, or page title
    body: str | None = None            # commit message, or page text
    url: str | None = None
    files: list[ChangedFile] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)

    @property
    def idem_key(self) -> str:
        return f"{self.source}:{self.object_ref}:{self.version}"


def enqueue(ev: Event) -> bool:
    """Insert an event. Returns False when it was a duplicate (same idem_key)."""
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into event (source, idem_key, payload, status, kind) "
            "values (%s, %s, %s, 'received', %s) on conflict (idem_key) do nothing "
            "returning id",
            (ev.source, ev.idem_key, ev.model_dump_json(), ev.kind))
        return cur.fetchone() is not None


def claim_next() -> tuple[str, Event] | None:
    """Claim one unprocessed event. SKIP LOCKED lets several workers run safely."""
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            update event set status = 'deduped', claimed_at = now(),
                             attempts = attempts + 1
             where id = (select id from event
                          where status = 'received' and attempts < 3
                          order by received_at
                          for update skip locked limit 1)
         returning id, payload""")
        row = cur.fetchone()
    if not row:
        return None
    payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return str(row[0]), Event.model_validate(payload)


def finish(event_id: str, status: str, outcome: dict | None = None,
           error: str | None = None) -> None:
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update event set status = %s, outcome = %s, error = %s, "
            "processed_at = now() where id = %s",
            (status, json.dumps(outcome) if outcome is not None else None, error, event_id))


def release(event_id: str, error: str) -> None:
    """A failed attempt goes back to the queue (up to 3 attempts, then 'failed')."""
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update event set status = case when attempts >= 3 then 'failed' "
            "else 'received' end, error = %s where id = %s", (error[:2000], event_id))
