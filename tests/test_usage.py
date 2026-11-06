"""Usage: dependency edges, stage lifted by usage, and 'used by' under RLS."""

from __future__ import annotations

import uuid

import pytest

from orgmem.collector.usage import _resolve, refresh
from orgmem.db import admin_conn, as_user


def test_resolve_matches_module_subpackages_and_major_versions():
    pub = {"example.com/acme/lib": "github:lib:.", "@acme/ui": "github:ui:."}
    assert _resolve(pub, "example.com/acme/lib") == "github:lib:."
    assert _resolve(pub, "example.com/acme/lib/v2/util") == "github:lib:."
    assert _resolve(pub, "@acme/ui") == "github:ui:."
    assert _resolve(pub, "example.com/other") is None


@pytest.fixture
def lib_and_two_users(tmp_path):
    tag = uuid.uuid4().hex[:6]
    names = {k: f"zz-use-{k}-{tag}" for k in ("lib", "app1", "app2")}
    (tmp_path / names["lib"]).mkdir()
    (tmp_path / names["lib"] / "go.mod").write_text(f"module example.com/acme/lib{tag}\n")
    for k in ("app1", "app2"):
        (tmp_path / names[k]).mkdir()
        (tmp_path / names[k] / "go.mod").write_text(
            f"module example.com/acme/{k}{tag}\n\nrequire (\n"
            f"\texample.com/acme/lib{tag} v0.0.0-20260101-abcdef\n"
            f"\texample.com/acme/unrelated v1.0.0 // indirect\n)\n")
    ids = {}
    with admin_conn() as conn, conn.cursor() as cur:
        for k, n in names.items():
            cur.execute("insert into team (name) values (%s) returning id", (f"team-{n}",))
            t = cur.fetchone()[0]
            cur.execute("insert into asset (kind, name, source_ref, unit_id, owner_team, "
                        "discovery_scope, stage) values ('library',%s,%s,%s,%s,%s,'dev') "
                        "returning id", (n, f"github:{n}", f"github:{n}:.", t, t))
            ids[k] = (cur.fetchone()[0], t)
    yield tmp_path, names, ids
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from asset where id = any(%s)", ([v[0] for v in ids.values()],))
        cur.execute("delete from team where id = any(%s)", ([v[1] for v in ids.values()],))
        cur.execute("delete from package_name where repo = any(%s)", (list(names.values()),))


def test_library_used_by_two_other_teams_is_lifted_to_prod(lib_and_two_users):
    root, names, ids = lib_and_two_users
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from edge where type = 'depends_on'")
        before_elsewhere = cur.fetchone()[0]
    out = refresh([root])
    assert out["edges"] == 2, "two direct users; the // indirect require is not a user"
    assert (f"github:{names['lib']}:.",
            "depended on by 2 other teams") in out["lifted"]
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select stage from asset where id = %s", (ids["lib"][0],))
        assert cur.fetchone()[0] == "prod"
        cur.execute("select count(*) from edge where type = 'depends_on'")
        assert cur.fetchone()[0] == before_elsewhere + 2, \
            "refreshing these repos must not delete other repos' edges"


def test_owner_confirmed_stage_is_never_changed_by_usage(lib_and_two_users):
    root, names, ids = lib_and_two_users
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("update asset set stage = 'beta', stage_confirmed = true where id = %s",
                    (ids["lib"][0],))
    refresh([root])
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select stage from asset where id = %s", (ids["lib"][0],))
        assert cur.fetchone()[0] == "beta"


def test_hidden_dependents_are_not_visible_through_edges(lib_and_two_users):
    """A user who can see the library but not app2 must not learn app2 depends on it."""
    root, names, ids = lib_and_two_users
    refresh([root])
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into app_user (login) values (%s) returning id",
                    (f"zz-viewer-{uuid.uuid4().hex[:6]}",))
        viewer = cur.fetchone()[0]
        for k in ("lib", "app1"):                       # sees lib and app1, not app2
            cur.execute("insert into membership values (%s,%s)", (viewer, ids[k][1]))
    try:
        with as_user(viewer) as conn, conn.cursor() as cur:
            cur.execute("select src from edge where dst = %s", (ids["lib"][0],))
            seen = {r[0] for r in cur.fetchall()}
        assert seen == {ids["app1"][0]}
    finally:
        with admin_conn() as conn, conn.cursor() as cur:
            cur.execute("delete from membership where user_id = %s", (viewer,))
            cur.execute("delete from app_user where id = %s", (viewer,))


def test_incremental_refresh_resolves_against_repos_scanned_earlier(lib_and_two_users):
    """Scale: each refresh reads ONE repo, yet the library is still found and lifted,
    because published names are stored (package_name) and stage uses all edges."""
    root, names, ids = lib_and_two_users
    out = refresh([root], repos={names["lib"]})
    assert out["scanned_repos"] == 1 and out["edges"] == 0
    out = refresh([root], repos={names["app1"]})
    assert out["scanned_repos"] == 1 and out["edges"] == 1, "resolved via stored name"
    out = refresh([root], repos={names["app2"]})
    assert (f"github:{names['lib']}:.", "depended on by 2 other teams") in out["lifted"]
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from edge where type = 'depends_on' and dst = %s",
                    (ids["lib"][0],))
        assert cur.fetchone()[0] == 2, "app2's refresh must keep app1's edge"
