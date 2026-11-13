"""Directory sync: snapshot import, hashed identities, merging, deactivation.

Runs in a transaction on the admin DSN that is rolled back, so the dev database is left
as it was.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from homegrown import directory_sync
from homegrown.collector import directory
from homegrown.config import settings


@pytest.fixture
def conn():
    c = psycopg.connect(settings.admin_dsn)
    try:
        c.execute("select 1")       # open the transaction, so apply() only savepoints
        yield c
    finally:
        c.rollback()
        c.close()


def _tag():
    return uuid.uuid4().hex[:8]


def _recs(t):
    return [
        {"id": f"m-{t}", "name": "Mona Manager", "team": f"zz-team-{t}", "lob": f"zz-lob-{t}",
         "manager_id": None, "emails": [f"mona.{t}@corp.example"],
         "salary": 1, "ssn": "x"},
        {"id": f"e-{t}", "name": "Eli Engineer", "team": f"zz-team-{t}",
         "lob": f"zz-lob-{t}", "manager_id": f"m-{t}", "active": True,
         "emails": [f"Eli.{t}@corp.example"], "logins": {"github": [f"Eli-Gh-{t}"]}},
    ]


def _one(conn, sql, *a):
    with conn.cursor() as cur:
        cur.execute(sql, a)
        return cur.fetchone()


def test_upsert_people_teams_managers_and_ignored_fields(conn):
    t = _tag()
    counts = directory_sync.apply(conn, _recs(t))
    assert counts["people"] == 2 and counts["managers_set"] == 1
    row = _one(conn, "select p.display_name, p.synthetic, p.active, p.synced_at, t.name, "
               "l.name, m.display_name from person p join team t on t.id = p.team_id "
               "join lob l on l.id = t.lob_id join person m on m.id = p.manager_id "
               "where p.external_id = %s", f"e-{t}")
    assert row[0] == "Eli Engineer" and row[1] is False and row[2] is True and row[3]
    assert row[4] == f"zz-team-{t}" and row[5] == f"zz-lob-{t}" and row[6] == "Mona Manager"
    directory_sync.apply(conn, _recs(t))        # a second run updates, never duplicates
    assert _one(conn, "select count(*) from person where external_id like %s",
                f"%-{t}")[0] == 2
    assert _one(conn, "select count(*) from person where display_name = '1' or "
                "title = 'x'")[0] == 0, "HR fields are not stored"


def test_identities_hash_like_resolve_and_nothing_raw_is_stored(conn):
    t = _tag()
    directory_sync.apply(conn, _recs(t))
    pid, team = _one(conn, "select id, team_id from person where external_id = %s",
                     f"e-{t}")
    assert directory.resolve(conn, "github", f"eli-gh-{t}", team) == pid
    assert directory.resolve(conn, "github", f"someone-else-{t}", team,
                             email=f"eli.{t}@CORP.example") == pid
    other = directory.resolve(conn, "github", f"nobody-{t}", team)
    assert other != pid
    # the email wins over a login that points elsewhere
    assert directory.resolve(conn, "github", f"nobody-{t}", team,
                             email=f"eli.{t}@corp.example") == pid
    h = directory._hash(f"github:eli-gh-{t}")
    assert _one(conn, "select person_id from source_identity where source='github' "
                "and login_hash=%s", h)[0] == pid
    assert _one(conn, "select person_id from source_identity where source='email' "
                "and login_hash=%s",
                directory._hash(f"email:eli.{t}@corp.example"))[0] == pid
    needle = f"%{t}%"
    assert _one(conn, "select count(*) from source_identity where login_hash ilike %s",
                needle)[0] == 0
    assert _one(conn, "select count(*) from person where handle ilike %s or "
                "display_name ilike %s or coalesce(title,'') ilike %s",
                f"%eli-gh-{t}%", "%corp.example%", "%corp.example%")[0] == 0
    assert _one(conn, "select count(*) from person where handle ilike %s",
                f"%eli.{t}%")[0] == 0


def test_a_new_pseudonymous_person_also_gets_an_email_identity(conn):
    t = _tag()
    team = _one(conn, "insert into team (name) values (%s) returning id", f"zz-t-{t}")[0]
    pid = directory.resolve(conn, "github", f"newbie-{t}", team, email=f"n.{t}@x.example")
    eh = directory.identity_hash("email", f"n.{t}@x.example")
    assert _one(conn, "select person_id from source_identity where source='email' and "
                "login_hash=%s", eh)[0] == pid
    noreply = f"1+x{t}@users.noreply.github.com"      # not an identity
    directory.resolve(conn, "github", f"other-{t}", team, email=noreply)
    assert _one(conn, "select count(*) from source_identity where login_hash=%s",
                directory.identity_hash("email", noreply))[0] == 0


def test_merge_moves_a_pseudonymous_persons_contributions(conn):
    t = _tag()
    team = _one(conn, "insert into team (name) values (%s) returning id", f"zz-t-{t}")[0]
    old = directory.resolve(conn, "github", f"eli-gh-{t}", team)
    ids = [_one(conn, "insert into asset (kind,name,source_ref,unit_id) values "
                "('service',%s,'github:zz',%s) returning id",
                f"zz-a{i}-{t}", f"github:zz-{t}:{i}")[0] for i in range(2)]
    for aid, c, first, last in ((ids[0], 4, "2026-01-01", "2026-05-01"),
                                (ids[1], 2, "2026-02-01", "2026-06-01")):
        conn.execute("insert into asset_contributor (asset_id, person_id, role, commits, "
                     "first_at, last_at) values (%s,%s,'author',%s,%s,%s)",
                     (aid, old, c, first, last))
    counts = directory_sync.apply(conn, _recs(t))
    new = _one(conn, "select id from person where external_id=%s", f"e-{t}")[0]
    assert counts["merged"] == 1 and new != old
    assert _one(conn, "select count(*) from asset_contributor where person_id=%s", old)[0] == 0
    assert _one(conn, "select count(*) from person where id=%s", old)[0] == 1, "not deleted"
    assert _one(conn, "select sum(commits) from asset_contributor where person_id=%s",
                new)[0] == 6
    # merging onto an existing row sums commits and widens the dates
    conn.execute("insert into asset_contributor (asset_id, person_id, role, commits, "
                 "first_at, last_at) values (%s,%s,'contributor',3,'2025-12-01','2026-03-01')",
                 (ids[0], old))
    conn.execute("update source_identity set person_id=%s where source='github' and "
                 "login_hash=%s", (old, directory._hash(f"github:eli-gh-{t}")))
    directory_sync.apply(conn, _recs(t))
    row = _one(conn, "select commits, first_at::date::text, last_at::date::text from "
               "asset_contributor where asset_id=%s and person_id=%s", ids[0], new)
    assert row == (7, "2025-12-01", "2026-05-01")


def test_people_absent_from_a_later_snapshot_are_deactivated_not_deleted(conn):
    t = _tag()
    directory_sync.apply(conn, _recs(t))
    counts = directory_sync.apply(conn, _recs(t)[:1])
    assert counts["deactivated"] >= 1
    assert _one(conn, "select active from person where external_id=%s", f"e-{t}")[0] is False
    assert _one(conn, "select active from person where external_id=%s", f"m-{t}")[0] is True
    directory_sync.apply(conn, _recs(t))        # comes back
    assert _one(conn, "select active from person where external_id=%s", f"e-{t}")[0] is True
    gone = _recs(t)
    gone[1]["active"] = False
    directory_sync.apply(conn, gone)
    assert _one(conn, "select active from person where external_id=%s", f"e-{t}")[0] is False


def test_an_app_user_is_linked_to_their_directory_person(conn):
    t = _tag()
    uid = _one(conn, "insert into app_user (login) values (%s) returning id",
               f"eli-gh-{t}")[0]
    directory_sync.apply(conn, _recs(t))
    pid = _one(conn, "select id from person where external_id=%s", f"e-{t}")[0]
    assert _one(conn, "select person_id from app_user where id=%s", uid)[0] == pid
