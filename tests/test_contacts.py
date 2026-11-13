"""Who to ask: recent committers in the directory, their managers, reasons from data.

Everything runs in a transaction on the admin DSN that is rolled back at the end, so the
dev database is left as it was. No model is involved.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from homegrown.config import settings
from homegrown.contacts import format_ask, who_to_ask


@pytest.fixture
def cur():
    conn = psycopg.connect(settings.admin_dsn)
    try:
        with conn.cursor() as c:
            c.execute("select exists (select 1 from person where not synthetic)")
            if c.fetchone()[0]:
                pytest.skip("the dev database holds a synced directory")
            yield c
    finally:
        conn.rollback()
        conn.close()


class World:
    def __init__(self, cur):
        self.cur = cur
        self.tag = uuid.uuid4().hex[:6]
        cur.execute("insert into team (name) values (%s) returning id", (f"zz-t-{self.tag}",))
        self.team = cur.fetchone()[0]
        self.n = 0

    def person(self, name, synthetic=True, active=True, manager=None):
        self.n += 1
        self.cur.execute(
            "insert into person (handle, display_name, team_id, manager_id, synthetic, "
            "active) values (%s,%s,%s,%s,%s,%s) returning id",
            (f"zz-{self.tag}-{self.n}", name, self.team, manager, synthetic, active))
        return self.cur.fetchone()[0]

    def asset(self, parent=None):
        self.n += 1
        self.cur.execute(
            "insert into asset (kind, name, source_ref, unit_id, owner_team, parent_id) "
            "values ('service',%s,'github:zz',%s,%s,%s) returning id",
            (f"zz-asset-{self.tag}-{self.n}", f"github:zz-{self.tag}:{self.n}", self.team,
             parent))
        return self.cur.fetchone()[0]

    def contrib(self, asset, person, commits, days_ago):
        last = datetime.now(UTC) - timedelta(days=days_ago) if days_ago is not None else None
        self.cur.execute(
            "insert into asset_contributor (asset_id, person_id, role, commits, last_at) "
            "values (%s,%s,'contributor',%s,%s)", (asset, person, commits, last))

    def lead(self):
        self.cur.execute(
            "insert into person (handle, display_name, team_id) values (%s,%s,%s)",
            (f"lead-{self.tag}", f"Lead, zz-t-{self.tag}", self.team))


def test_most_recent_committer_first_then_commits_break_ties(cur):
    w = World(cur)
    boss = w.person("Boss")
    a, b, c, d = (w.person(n, manager=boss) for n in "ABCD")
    asset = w.asset()
    w.contrib(asset, a, 50, 200)
    w.contrib(asset, b, 3, 5)
    w.contrib(asset, c, 9, 5)       # same day as B, more commits
    w.contrib(asset, d, 99, None)   # no date sorts last
    got = who_to_ask(cur, asset)
    assert [p["name"] for p in got] == ["C", "B"]
    assert got[0]["manager"] == "Boss" and got[0]["team"] == f"zz-t-{w.tag}"
    assert got[0]["commits"] == 9 and got[0]["handle"].startswith("zz-")
    assert got[0]["reason"].startswith("last commit ") and "9 commits" in got[0]["reason"]
    assert [p["name"] for p in who_to_ask(cur, asset, limit=4)] == ["C", "B", "A", "D"]


def test_asker_and_inactive_people_are_skipped(cur):
    w = World(cur)
    me, gone, ok1, ok2 = (w.person("Me"), w.person("Gone", active=False), w.person("Ok1"),
                          w.person("Ok2"))
    asset = w.asset()
    for i, p in enumerate((me, gone, ok1, ok2)):
        w.contrib(asset, p, 5, 1 + i)
    assert [p["name"] for p in who_to_ask(cur, asset, asker_person_id=me)] == ["Ok1", "Ok2"]
    assert [p["name"] for p in who_to_ask(cur, asset, asker_person_id=str(me))] == \
        ["Ok1", "Ok2"]


def test_with_a_synced_directory_only_directory_people_count(cur):
    w = World(cur)
    synth = w.person("Pseudo")
    real = w.person("Real", synthetic=False)
    asset = w.asset()
    w.contrib(asset, synth, 40, 1)
    w.contrib(asset, real, 2, 30)
    assert [p["name"] for p in who_to_ask(cur, asset)] == ["Real"]


def test_stale_note_when_the_newest_commit_is_old(cur):
    w = World(cur)
    asset = w.asset()
    w.contrib(asset, w.person("Old"), 1, 800)
    r = who_to_ask(cur, asset)[0]["reason"]
    assert r.endswith("; no recent activity") and ", 1 commit;" in r
    assert "no recent activity" not in who_to_ask(cur, asset, stale_days=1000)[0]["reason"]


def test_falls_back_to_the_team_lead_and_then_to_nothing(cur):
    w = World(cur)
    asset = w.asset()
    assert who_to_ask(cur, asset) == []
    w.lead()
    got = who_to_ask(cur, asset)
    assert len(got) == 1 and got[0]["name"].startswith("Lead,")
    assert got[0]["manager"] is None and got[0]["commits"] is None
    assert got[0]["reason"] == ("no recent committer found in the directory; "
                                "lead of the owning team")
    # an inactive-only contributor list also falls back
    w.contrib(asset, w.person("Gone", active=False), 5, 1)
    assert who_to_ask(cur, asset)[0]["name"].startswith("Lead,")


def test_a_crawler_unit_uses_its_repos_contributors(cur):
    w = World(cur)
    repo = w.asset()
    w.contrib(repo, w.person("Maker"), 7, 2)
    unit = w.asset(parent=repo)
    assert [p["name"] for p in who_to_ask(cur, unit)] == ["Maker"]
    w.contrib(unit, w.person("Own"), 1, 1)
    assert [p["name"] for p in who_to_ask(cur, unit)] == ["Own"]


def test_format_ask_line():
    line = format_ask({"name": "Ada", "team": "Pay", "manager": "Bo",
                       "reason": "last commit 2026-10-30, 14 commits"})
    assert line == "Ada (Pay), manager Bo: last commit 2026-10-30, 14 commits"
    assert format_ask({"name": "Ada", "team": None, "manager": None, "reason": "x"}) == \
        "Ada: x"
