"""The permission leakage gate. Must pass on every change.

The design requires this, and specifically against the
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

from homegrown.db import admin_conn, as_user


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
            for _aid, scope in cur.fetchall():
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
            for _asset_id, source_ref, has_access, detail in cur.fetchall():
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
        cur.execute("select rolsuper, rolbypassrls from pg_roles where rolname='homegrown_app'")
        is_super, bypass = cur.fetchone()
        assert not is_super, "app role is a superuser: RLS is bypassed entirely"
        assert not bypass, "app role has BYPASSRLS"


def test_search_never_returns_out_of_scope_results(corpus):
    """End-to-end: the actual read path, not just raw tables."""
    from homegrown.search import find_existing

    queries = ["reject a payload that is too large", "retry a failed request",
               "parse configuration", "clamp a value to a range", "kafka consumer"]
    for u in corpus["users"]:
        entitled = {str(a["id"]) for a in corpus["assets"] if a["scope"] in u["groups"]}
        for q in queries:
            for v in find_existing(u["id"], q):
                assert v.asset_id in entitled, (
                    f"{u['login']}: search returned out-of-scope asset for {q!r}")


# ------------------------------------------------------------------ fts_hits()
# Indexed keyword search bypasses RLS internally (db/008_fts.sql) and re-applies the
# visibility rules itself. These tests hold it to exactly the policies' rules.

def test_fts_hits_never_returns_an_undiscoverable_asset(corpus):
    from homegrown.search import _tsquery_or
    queries = ["retry backoff http", "kafka consumer", "time series outlier",
               "dashboard panel plugin", "alert rule notification", "parse configuration"]
    # Plus the corpus's own asset names, so the test has hits on any corpus.
    queries += [a["name"].rsplit("/", 1)[-1] for a in corpus["assets"][:10]]
    checked = 0
    for u in corpus["users"]:
        entitled = {a["id"] for a in corpus["assets"] if a["scope"] in u["groups"]}
        with as_user(u["id"]) as conn:
            for q in queries:
                for (aid,) in conn.execute(
                        "select asset_id from fts_hits(to_tsquery('english', %s), 1000)",
                        (_tsquery_or(q),)).fetchall():
                    checked += 1
                    assert aid in entitled, f"{u['login']}: fts_hits leaked an asset for {q!r}"
    assert checked > 0, "test checked nothing"


def test_fts_hits_sensitive_claim_words_need_source_access():
    """A sensitive claim's words must not surface its asset to someone who can discover
    the asset but can't read its source, and must for someone who can."""
    import uuid
    tag = uuid.uuid4().hex[:8]
    word = f"zzsecret{tag}"
    with admin_conn() as conn:
        team = conn.execute("insert into team (name) values (%s) returning id",
                            (f"team-fts-{tag}",)).fetchone()[0]
        reader, outsider = (conn.execute(
            "insert into app_user (login, team_id) values (%s,%s) returning id",
            (f"fts-{k}-{tag}", team)).fetchone()[0] for k in ("reader", "outsider"))
        for u in (reader, outsider):
            conn.execute("insert into membership (user_id, group_id) values (%s,%s)", (u, team))
        src = f"github:fts-{tag}"
        conn.execute("insert into source_acl (user_id, source_ref) values (%s,%s)", (reader, src))
        aid = conn.execute(
            "insert into asset (kind, name, source_ref, unit_id, owner_team, discovery_scope, "
            "summary) values ('library', %s, %s, %s, %s, %s, 'a plain library') returning id",
            (f"fts-asset-{tag}", src, f"{src}:.", team, team)).fetchone()[0]
        ev = conn.execute(
            "insert into evidence (source, ref, source_version, content_hash, extractor_version) "
            "values ('github_code', 'x', 'v', 'h', 't') returning id").fetchone()[0]
        conn.execute(
            "insert into claim (asset_id, layer, kind, body, evidence_id, extractor, confidence, "
            "sensitive) values (%s, 3, 'pitfall', %s, %s, 'test', 0.9, true)",
            (aid, f"internal note mentions {word}", ev))
    try:
        def hits(user):
            with as_user(user) as conn:
                return [r[0] for r in conn.execute(
                    "select asset_id from fts_hits(to_tsquery('english', %s), 10)",
                    (word,)).fetchall()]
        assert hits(outsider) == [], "sensitive text surfaced to a user without source access"
        assert hits(reader) == [aid]
        assert hits(None) == [], "an unidentified caller must get nothing"
    finally:
        with admin_conn() as conn:
            conn.execute("delete from asset where id = %s", (aid,))
            conn.execute("delete from source_acl where source_ref = %s", (src,))
            conn.execute("delete from membership where group_id = %s", (team,))
            conn.execute("delete from app_user where team_id = %s", (team,))
            conn.execute("delete from team where id = %s", (team,))
