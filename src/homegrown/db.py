"""Database access.

The only interesting thing here is `as_user`. Every read on the read path must run
with `app.user_id` set, because that is what every row-level security policy keys on.
With it unset, `current_app_user()` returns NULL and the policies return nothing --
fail closed, which is the behaviour we want if a caller is ever unidentified.
"""

from __future__ import annotations

import atexit
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import psycopg
from pgvector.psycopg import register_vector

from .config import settings

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

# Connection pools, one per DSN, created on first use. Pooling is safe with row-level
# security: as_user() sets app.user_id for one transaction only, so it can't leak into
# the next request that reuses the connection.
_pools: dict[tuple[str, bool], ConnectionPool] = {}
_pool_lock = threading.Lock()


def _pool(dsn: str, autocommit: bool):
    key = (dsn, autocommit)
    pool = _pools.get(key)
    if pool is None:
        with _pool_lock:
            pool = _pools.get(key)
            if pool is None:
                from psycopg_pool import ConnectionPool
                pool = ConnectionPool(dsn, min_size=1, max_size=settings.db_pool_size,
                                      kwargs={"autocommit": autocommit},
                                      configure=register_vector, open=True)
                _pools[key] = pool
    return pool


@atexit.register
def close_pools() -> None:
    for pool in list(_pools.values()):
        pool.close()
    _pools.clear()


@contextmanager
def admin_conn() -> Iterator[psycopg.Connection]:
    """Schema owner. Bypasses RLS -- use for ingestion and migrations only."""
    with _pool(settings.admin_dsn, True).connection() as conn:
        yield conn


@contextmanager
def app_conn() -> Iterator[psycopg.Connection]:
    """Non-superuser, NOBYPASSRLS. Use for everything on the read path.
    Commits on success and rolls back on error, like psycopg.connect()."""
    with _pool(settings.app_dsn, False).connection() as conn:
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
