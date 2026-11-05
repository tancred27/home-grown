"""The permission leakage gate. Must pass on every change.

docs/design.md section 8 requires this, and it specifically requires it against the
REAL schema. The earlier harness (experiments/expb.py) only proved that Postgres
enforces a single `scope in (...)` policy -- it never touched the parts this design
actually depends on:

  * the fields / detail split on card_version
  * the source_acl join that gates `detail`
  * sensitive claims being withheld without source access
  * edges where one end is invisible
  * counts computed after filtering, not before
  * fail-closed behaviour when the caller is unidentified

Each of those is a separate test below. Run: .venv/Scripts/python -m pytest -q
"""

from __future__ import annotations

import pytest

from orgmem.db import admin_conn, as_user


@pytest.fixture(scope="module")
def corpus():
    """Every user, with their team and the assets they are entitled to see."""
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from asset")
        total = cur.fetchone()[0]
        if total == 0:
            pytest.skip("no corpus; run scripts/ingest.py first")
        cur.execute("""
            select u.id, u.login,
                   array(select m.group_id from membership m where m.user_id = u.id),
                   array(select sa.source_ref from source_acl sa where sa.user_id = u.id)
              from app_user u order by u.login
        """)
        users = [{"id": r[0], "login": r[1], "groups": set(r[2]), "sources": set(r[3])}
                 for r in cur.fetchall()]
        cur.execute("select id, name, discovery_scope, source_ref from asset")
        assets = [{"id": r[0], "name": r[1], "scope": r[2], "source_ref": r[3]}
                  for r in cur.fetchall()]
    return {"total": total, "users": users, "assets": assets}


def test_asset_rows_are_always_in_scope(corpus):
    """No user may ever receive an asset row outside their discovery scope."""
    checked = leaks = 0
    for u in corpus["users"]:
        with as_user(u["id"]) as conn, conn.cursor() as cur:
            cur.execute("select id, discovery_scope from asset")
            for aid, scope in cur.fetchall():
                checked += 1
                if scope not in u["groups"]:
                    leaks += 1
    assert checked > 0, "test checked nothing"
    assert leaks == 0, f"{leaks} asset rows leaked across the boundary ({checked} checked)"


def test_counts_are_computed_after_filtering(corpus):
    """A count must not reveal the existence of invisible assets."""
    for u in corpus["users"]:
        entitled = sum(1 for a in corpus["assets"] if a["scope"] in u["groups"])
        with as_user(u["id"]) as conn, conn.cursor() as cur:
            cur.execute("select count(*) from asset")
            assert cur.fetchone()[0] == entitled, (
                f"{u['login']}: count revealed rows outside scope")


def test_detail_requires_source_access(corpus):
    """card_visible.detail must be empty without source_acl on the asset's source."""
    for u in corpus["users"]:
        with as_user(u["id"]) as conn, conn.cursor() as cur:
            cur.execute("""
                select cv.asset_id, a.source_ref, cv.has_source_access, cv.detail
                  from card_visible cv join asset a on a.id = cv.asset_id
            """)
            for asset_id, source_ref, has_access, detail in cur.fetchall():
                expected = source_ref in u["sources"]
                assert has_access == expected, (
                    f"{u['login']}: has_source_access wrong for {source_ref}")
                if not expected:
                    assert detail == {}, (
                        f"{u['login']}: detail leaked for {source_ref} without access")


def test_sensitive_claims_withheld_without_source_access(corpus):
    """Layer-3 prose is sensitive. It must not appear without source access."""
    for u in corpus["users"]:
        with as_user(u["id"]) as conn, conn.cursor() as cur:
            cur.execute("""
                select c.id, a.source_ref
                  from claim c join asset a on a.id = c.asset_id
                 where c.sensitive and c.valid_to is null
            """)
            for claim_id, source_ref in cur.fetchall():
                assert source_ref in u["sources"], (
                    f"{u['login']}: sensitive claim {claim_id} leaked for {source_ref}")


def test_edges_never_name_an_invisible_asset(corpus):
    """An edge is only returned when both ends are visible."""
    for u in corpus["users"]:
        with as_user(u["id"]) as conn, conn.cursor() as cur:
            cur.execute("select src, dst from edge")
            visible = {a["id"] for a in corpus["assets"] if a["scope"] in u["groups"]}
            for src, dst in cur.fetchall():
                assert src in visible and dst in visible, (
                    f"{u['login']}: edge exposed an invisible asset")


def test_unidentified_caller_sees_nothing(corpus):
    """Fail closed. With app.user_id unset, every policy must return zero rows."""
    with as_user(None) as conn, conn.cursor() as cur:
        for table in ("asset", "claim", "card_version", "embedding", "edge"):
            cur.execute(f"select count(*) from {table}")
            assert cur.fetchone()[0] == 0, f"{table} returned rows to an unidentified caller"


def test_app_role_cannot_bypass_rls():
    """The guarantee everything else rests on."""
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select rolsuper, rolbypassrls from pg_roles where rolname='orgmem_app'")
        is_super, bypass = cur.fetchone()
        assert not is_super, "app role is a superuser: RLS is bypassed entirely"
        assert not bypass, "app role has BYPASSRLS"


def test_search_never_returns_out_of_scope_results(corpus):
    """End-to-end: the actual read path, not just raw tables."""
    from orgmem.search import find_existing

    queries = ["reject a payload that is too large", "retry a failed request",
               "parse configuration", "clamp a value to a range", "kafka consumer"]
    for u in corpus["users"]:
        entitled = {str(a["id"]) for a in corpus["assets"] if a["scope"] in u["groups"]}
        for q in queries:
            for v in find_existing(u["id"], q):
                assert v.asset_id in entitled, (
                    f"{u['login']}: search returned out-of-scope asset for {q!r}")
