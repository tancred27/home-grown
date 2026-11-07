"""HNSW search mode must give the same answers as exact scan, under RLS."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from homegrown.config import settings
from homegrown.db import admin_conn, as_user
from homegrown.search import search

ROOT = Path(__file__).parent.parent


@pytest.fixture
def sees_everything():
    login = f"zz-modes-{uuid.uuid4().hex[:6]}"
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into team (name) values (%s) returning id", (f"team-{login}",))
        t = cur.fetchone()[0]
        cur.execute("insert into app_user (login, team_id) values (%s,%s) returning id",
                    (login, t))
        u = cur.fetchone()[0]
        cur.execute("insert into membership (user_id, group_id) select %s, id from team", (u,))
    yield u
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from membership where user_id = %s", (u,))
        cur.execute("delete from app_user where id = %s", (u,))
        cur.execute("delete from team where id = %s", (t,))


def test_hnsw_mode_matches_exact_scan(sees_everything, monkeypatch):
    qs = [q["query"] for q in json.loads(
        (ROOT / "experiments/gate1/queries.json").read_text("utf-8"))[:25]]
    top = {}
    for mode in ("exact", "hnsw"):
        monkeypatch.setattr(settings, "vector_search", mode)
        with as_user(sees_everything) as conn:
            top[mode] = [[r["asset_id"] for r in search(conn, q)[:5]] for q in qs]
    same = sum(1 for a, b in zip(top["exact"], top["hnsw"], strict=True) if set(a) == set(b))
    assert same >= 0.9 * len(qs), f"hnsw top-5 matched exact for only {same}/{len(qs)}"


def test_hnsw_mode_never_shows_a_hidden_asset(monkeypatch):
    """A user in no team sees nothing, even through the index."""
    monkeypatch.setattr(settings, "vector_search", "hnsw")
    with as_user(None) as conn:
        assert search(conn, "retry http requests with backoff") == []


def test_auto_mode_switches_on_table_size(monkeypatch):
    import homegrown.search as S
    monkeypatch.setattr(settings, "vector_search", "auto")
    with as_user(None) as conn, conn.cursor() as cur:
        for threshold, expected in ((10**12, "exact"), (-1, "hnsw")):  # never-analysed: -1 rows
            S._mode_cache.clear()
            monkeypatch.setattr(settings, "hnsw_auto_threshold", threshold)
            assert S.vector_mode(cur) == expected
    S._mode_cache.clear()
    monkeypatch.setattr(settings, "vector_search", "exact")
    with as_user(None) as conn, conn.cursor() as cur:
        assert S.vector_mode(cur) == "exact", "an explicit setting always wins"
