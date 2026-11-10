"""The indexed bm25_hits() (db/013_bm25_index.sql) returns exactly what 009's did, and its
indexes follow every change to asset text and claims."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from homegrown.db import admin_conn, as_user
from homegrown.search import _tsquery_or

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def reference():
    """009's bm25_hits, recreated as bm25_hits_ref for comparison, dropped afterwards."""
    sql = (ROOT / "db" / "009_bm25.sql").read_text("utf-8")
    i = sql.index("create or replace function bm25_hits(")
    body = sql[i:sql.index("$$;", i) + 3].replace("function bm25_hits(",
                                                   "function bm25_hits_ref(", 1)
    sig = "bm25_hits_ref(tsquery, text[], int, real, real)"
    with admin_conn() as conn:
        conn.execute(body)
        conn.execute(f"alter function {sig} owner to homegrown_search")
        conn.execute(f"grant execute on function {sig} to homegrown_app")
    yield
    with admin_conn() as conn:
        conn.execute(f"drop function if exists {sig}")


def _hits(conn, fn: str, text: str) -> dict:
    tsq = _tsquery_or(text)
    return {a: s for a, s in conn.execute(
        f"select asset_id, score from {fn}(to_tsquery('english', %s), "
        "coalesce(tsvector_to_array(to_tsvector('english', %s)), '{}'), 1000)",
        (tsq, tsq.replace("|", " "))).fetchall()}


QUERIES = ["retry backoff http", "kafka consumer producer", "time series outlier detection",
           "dashboard panel plugin", "alert rule notification", "parse configuration file",
           "kind cluster dual stack networking", "refund invoice pdf"]


def test_indexed_bm25_matches_the_reference_for_every_user(reference):
    with admin_conn() as conn:
        users = [r[0] for r in conn.execute("select id from app_user order by login limit 40")]
    if not users:
        pytest.skip("no users")
    compared = 0
    for u in users:
        with as_user(u) as conn:
            for q in QUERIES:
                new, ref = _hits(conn, "bm25_hits", q), _hits(conn, "bm25_hits_ref", q)
                assert set(new) == set(ref), f"different hits for {q!r}"
                for a in ref:
                    assert new[a] == pytest.approx(ref[a], rel=1e-4), f"score differs for {q!r}"
                compared += len(ref)
    assert compared > 0, "test compared nothing"


@pytest.fixture
def one_asset():
    tag = uuid.uuid4().hex[:8]
    with admin_conn() as conn:
        team = conn.execute("insert into team (name) values (%s) returning id",
                            (f"team-bmidx-{tag}",)).fetchone()[0]
        user = conn.execute("insert into app_user (login, team_id) values (%s,%s) returning id",
                            (f"bmidx-{tag}", team)).fetchone()[0]
        conn.execute("insert into membership (user_id, group_id) values (%s,%s)", (user, team))
        ev = conn.execute(
            "insert into evidence (source, ref, source_version, content_hash, extractor_version) "
            "values ('github_code', 'x', 'v', 'h', 't') returning id").fetchone()[0]
        aid = conn.execute(
            "insert into asset (kind, name, source_ref, unit_id, owner_team, discovery_scope, "
            "summary) values ('library', %s, %s, %s, %s, %s, %s) returning id",
            (f"bmidx-{tag}", f"github:bmidx-{tag}", f"github:bmidx-{tag}:.", team, team,
             f"zzold{tag} handler")).fetchone()[0]
    yield {"tag": tag, "team": team, "user": user, "ev": ev, "asset": aid}
    with admin_conn() as conn:
        conn.execute("delete from asset where id = %s", (aid,))
        conn.execute("delete from evidence where id = %s", (ev,))
        conn.execute("delete from membership where user_id = %s", (user,))
        conn.execute("delete from app_user where id = %s", (user,))
        conn.execute("delete from team where id = %s", (team,))


def test_index_follows_summary_and_claim_changes(reference, one_asset):
    t = one_asset
    old, new, cw = f"zzold{t['tag']}", f"zznew{t['tag']}", f"zzclaim{t['tag']}"

    def both(word):
        with as_user(t["user"]) as conn:
            return _hits(conn, "bm25_hits", word), _hits(conn, "bm25_hits_ref", word)

    with admin_conn() as conn:                      # the summary changes: tsv via trigger
        conn.execute("update asset set summary = %s where id = %s", (f"{new} handler", t["asset"]))
    assert both(old) == ({}, {})
    n, r = both(new)
    assert list(n) == [t["asset"]] and n[t["asset"]] == pytest.approx(r[t["asset"]], rel=1e-4)

    with admin_conn() as conn:                      # a claim arrives, then is closed
        cid = conn.execute(
            "insert into claim (asset_id, layer, kind, body, evidence_id, extractor, confidence) "
            "values (%s, 1, 'capability', %s, %s, 'test', 0.6) returning id",
            (t["asset"], f"handles {cw} payloads", t["ev"])).fetchone()[0]
    n, r = both(f"{new} {cw}")
    assert n[t["asset"]] == pytest.approx(r[t["asset"]], rel=1e-4)
    with admin_conn() as conn:
        conn.execute("update claim set valid_to = now() where id = %s", (cid,))
    assert both(cw) == ({}, {})
    n, r = both(new)                                # document length shrank back
    assert n[t["asset"]] == pytest.approx(r[t["asset"]], rel=1e-4)


def test_deleting_an_asset_with_claims_cleans_the_index(one_asset):
    t = one_asset
    with admin_conn() as conn:
        conn.execute(
            "insert into claim (asset_id, layer, kind, body, evidence_id, extractor, confidence) "
            "values (%s, 1, 'capability', 'temporary claim text', %s, 'test', 0.6)",
            (t["asset"], t["ev"]))
        conn.execute("delete from asset where id = %s", (t["asset"],))
        left = conn.execute(
            "select (select count(*) from asset_terms where asset_id = %s), "
            "(select count(*) from claim_terms where asset_id = %s), "
            "(select count(*) from asset_claim_len where asset_id = %s)",
            (t["asset"], t["asset"], t["asset"])).fetchone()
    assert left == (0, 0, 0)
