"""Database access.

The only interesting thing here is `as_user`. Every read on the read path must run
with `app.user_id` set, because that is what every row-level security policy keys on.
With it unset, `current_app_user()` returns NULL and the policies return nothing --
fail closed, which is the behaviour we want if a caller is ever unidentified.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from .config import settings


@contextmanager
def admin_conn() -> Iterator[psycopg.Connection]:
    """Schema owner. Bypasses RLS -- use for ingestion and migrations only."""
    with psycopg.connect(settings.admin_dsn, autocommit=True) as conn:
        register_vector(conn)
        yield conn


@contextmanager
def app_conn() -> Iterator[psycopg.Connection]:
    """Non-superuser, NOBYPASSRLS. Use for everything on the read path."""
    with psycopg.connect(settings.app_dsn) as conn:
        register_vector(conn)
        yield conn


@contextmanager
def as_user(user_id: uuid.UUID | str | None) -> Iterator[psycopg.Connection]:
    """An app connection acting as one user, for the duration of one transaction.

    set_config(..., true) makes the setting transaction-local, so it cannot leak into
    another request on a pooled connection.
    """
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select set_config('app.user_id', %s, true)",
                (str(user_id) if user_id else "",),
            )
        yield conn
        conn.rollback()  # read path never writes; make that explicit


def fetch_all(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(conn: psycopg.Connection, sql: str, params: tuple = ()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()
