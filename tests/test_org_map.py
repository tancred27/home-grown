"""The org map and the dashboard endpoints.

The org map lists components, so it is held to the same rule as search: a caller sees
only what row-level security lets them discover, and every count is computed after
that filter.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from homegrown.api import app
from homegrown.db import admin_conn
from homegrown.orgmap import org_map

client = TestClient(app)


@pytest.fixture(scope="module")
def corpus():
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from asset where kind <> 'solved_problem'")
        if cur.fetchone()[0] == 0:
            pytest.skip("no corpus; run scripts/ingest.py first")
        cur.execute("""
            select u.id, u.login,
                   array(select m.group_id from membership m where m.user_id = u.id),
                   array(select sa.source_ref from source_acl sa where sa.user_id = u.id)
              from app_user u order by u.login""")
        users = [{"id": str(r[0]), "login": r[1], "groups": set(r[2]), "sources": set(r[3])}
                 for r in cur.fetchall()]
        cur.execute("select id, discovery_scope, source_ref from asset "
                    "where kind <> 'solved_problem'")
        assets = [{"id": str(r[0]), "scope": r[1], "source_ref": r[2]}
                  for r in cur.fetchall()]
    return {"users": users, "assets": assets}


def _components(m: dict) -> list[dict]:
    return [c for lob in m["lobs"] for c in lob["components"]]


def test_org_map_lists_exactly_what_the_caller_may_discover(corpus):
    for u in corpus["users"][:40]:
        entitled = {a["id"] for a in corpus["assets"] if a["scope"] in u["groups"]}
        m = org_map(u["id"])
        listed = {c["id"] for c in _components(m)}
        assert listed == entitled, f"{u['login']}: org map differs from the policy"


def test_org_map_counts_are_computed_after_filtering(corpus):
    for u in corpus["users"][:40]:
        m = org_map(u["id"])
        comps = _components(m)
        assert m["components"] == len(comps)
        assert sum(m["by_stage"].values()) == len(comps)
        assert m["repositories"] == len({c["repo"] for c in comps})
        for lob in m["lobs"]:
            assert lob["teams"] == len({c["team"] for c in lob["components"] if c["team"]})


def test_code_access_follows_source_acl(corpus):
    for u in corpus["users"][:40]:
        for c in _components(org_map(u["id"])):
            assert c["can_read_code"] == (c["repo"] in u["sources"]), (
                f"{u['login']}: wrong code access for {c['name']}")


def test_unidentified_caller_sees_nothing():
    m = org_map(None)
    assert m["components"] == 0 and m["lobs"] == []


def test_org_map_endpoint_requires_a_known_user(corpus):
    assert client.get("/api/org-map").status_code == 401
    r = client.get("/api/org-map", headers={"X-Homegrown-User": "no-such-user-xyz"})
    assert r.status_code == 403


def test_org_map_endpoint_answers_as_the_caller(corpus):
    u = corpus["users"][0]
    r = client.get("/api/org-map", headers={"X-Homegrown-User": u["login"]})
    assert r.status_code == 200
    assert r.json()["components"] == org_map(u["id"])["components"]


def test_dashboard_page_is_served():
    r = client.get("/ui")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "/api/org-map" in r.text
