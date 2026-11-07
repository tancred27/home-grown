"""State-based sync: real git repos, fingerprints, the scheduler rule, leases, races."""

from __future__ import annotations

import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from homegrown.collector.source import LocalGitSource
from homegrown.collector.state import claim_due, complete, mark_dirty
from homegrown.collector.sync import Syncer, read_kind, unit_dirs
from homegrown.config import settings
from homegrown.db import admin_conn

T0 = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def git(repo: Path, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", "-C", str(repo), *args],
                   check=True, capture_output=True)


def write(repo: Path, rel: str, text: str):
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


@pytest.fixture
def mono(tmp_path):
    """A monorepo with two units, as a real git repo on branch main."""
    name = f"zz-sync-{uuid.uuid4().hex[:6]}"
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "README.md", f"# {name}\n")
    for u, what in (("a", "retries HTTP calls"), ("b", "parses durations")):
        write(repo, f"pkg/{u}/package.json",
              '{"name": "%s-%s", "description": "Library that %s"}' % (name, u, what))
        write(repo, f"pkg/{u}/README.md", f"# {u}\n\nThis library {what}.\n")
        write(repo, f"pkg/{u}/src/index.ts", "export const x = 1\n")
        write(repo, f"pkg/{u}/{u}.test.ts", f"it('handles the basic case for {u}', () => {{}})\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    full = f"testorg/{name}"
    yield full, repo
    with admin_conn() as conn:
        conn.execute("delete from asset where unit_id like %s", (f"github:{name}:%",))
        conn.execute("delete from unit_fingerprint where repo = %s", (full,))
        conn.execute("delete from repo_state where repo = %s", (full,))


def test_read_kind_and_unit_dirs():
    assert read_kind("pkg/a/package.json") == "manifest"
    assert read_kind("pkg/a/README.md") == "readme"
    assert read_kind("pkg/a/a.test.ts") == "test"
    assert read_kind("pkg/a/src/index.ts") is None
    assert read_kind("node_modules/x/package.json") is None
    assert unit_dirs({"pkg/a/package.json": "1", "pkg/a/b/go.mod": "2", "x.ts": "3"}) == \
        ["pkg/a/b", "pkg/a", "."]


def test_only_changed_units_are_reprocessed(mono):
    full, repo = mono
    s = Syncer(LocalGitSource({full: repo}))
    first = s.sync(full, None)
    assert first["status"] == "processed" and first["changed_units"] == 3   # a, b, root

    assert s.sync(full, first["head"])["status"] == "unchanged", "same head: 1 call, stop"

    write(repo, "pkg/a/src/index.ts", "export const x = 2\n")             # source only
    git(repo, "commit", "-qam", "tweak source")
    r = s.sync(full, first["head"])
    assert r["status"] == "fingerprints-only" and r["changed_units"] == 0, \
        "a source-only change alters nothing the collector reads"

    write(repo, "pkg/a/a.test.ts",
          "it('handles the basic case for a', () => {})\nit('rejects a negative delay', () => {})\n")
    git(repo, "commit", "-qam", "new test in a")
    r2 = s.sync(full, r["head"])
    assert r2["status"] == "processed" and r2["changed_units"] == 1, "only unit a"
    assert r2["files_changed"] == 1


def test_revert_and_force_push_are_just_new_states(mono):
    full, repo = mono
    s = Syncer(LocalGitSource({full: repo}))
    base = s.sync(full, None)["head"]
    write(repo, "pkg/b/README.md", "# b\n\nNow also formats durations.\n")
    git(repo, "commit", "-qam", "docs b")
    changed = s.sync(full, base)
    assert changed["changed_units"] == 1
    git(repo, "revert", "--no-edit", "HEAD")                              # revert
    back = s.sync(full, changed["head"])
    assert back["changed_units"] == 1, "state differs from the stored one again"
    git(repo, "reset", "-q", "--hard", base)                              # force-push
    forced = s.sync(full, back["head"])
    assert forced["status"] == "fingerprints-only", \
        "rewound to a state whose readable files equal the stored ones"


def test_ci_status_is_kept_as_information(mono):
    full, repo = mono

    class WithCI(LocalGitSource):
        def ci(self, repo, sha):
            return "failure"
    Syncer(WithCI({full: repo})).sync(full, None)
    name = full.split("/")[1]
    with admin_conn() as conn:
        rows = conn.execute("select ci_status from asset where unit_id like %s",
                            (f"github:{name}:%",)).fetchall()
    assert rows and all(r[0] == "failure" for r in rows), "stored, not used as a gate"


def test_scheduler_quiet_or_max_wait(mono, monkeypatch):
    full, _ = mono
    monkeypatch.setattr(settings, "sync_quiet_s", 120)
    monkeypatch.setattr(settings, "sync_max_wait_s", 480)
    mark_dirty(full, T0)
    assert _claim_for(full, T0 + timedelta(seconds=60)) is None, "not quiet yet"
    job = _claim_for(full, T0 + timedelta(seconds=130))
    assert job is not None, "quiet for 2 minutes: due"
    complete(full, "w1", "sha1", job["started_at"], now=T0 + timedelta(seconds=140))

    # A busy repo that never goes quiet is still processed within max_wait.
    for k in range(0, 600, 60):
        mark_dirty(full, T0 + timedelta(seconds=200 + k))
    assert _claim_for(full, T0 + timedelta(seconds=200 + 470)) is None
    assert _claim_for(full, T0 + timedelta(seconds=200 + 490)) is not None


def test_lease_one_worker_per_repo_and_mid_run_changes(mono):
    full, _ = mono
    mark_dirty(full, T0)
    now = T0 + timedelta(seconds=600)
    job = _claim_for(full, now, owner="w1")
    assert job is not None
    assert _claim_for(full, now, owner="w2") is None, "leased: a second worker skips it"
    mark_dirty(full, now + timedelta(seconds=5))                          # commit mid-run
    assert complete(full, "w1", "sha1", job["started_at"], now=now + timedelta(seconds=10))
    with admin_conn() as conn:
        dirty, = conn.execute("select dirty from repo_state where repo = %s", (full,)).fetchone()
    assert dirty, "a change during the run keeps the repo dirty for the next pass"
    assert not complete(full, "w2", "sha2", now), "only the lease holder can complete"
    later = now + timedelta(seconds=settings.sync_lease_s + 1000)
    assert _claim_for(full, later, owner="w3") is not None, "an expired lease is reclaimed"


def _claim_for(repo, now, owner="w1"):
    """claim_due, but only accept our own repo (other tests' rows may be due too)."""
    for _ in range(50):
        job = claim_due(owner, now=now)
        if job is None or job["repo"] == repo:
            return job
        with admin_conn() as conn:                 # not ours: release it untouched
            conn.execute("update repo_state set lease_owner = null, lease_until = null "
                         "where repo = %s", (job["repo"],))
    return None


def test_poller_marks_only_repos_pushed_since_last_poll():
    from homegrown.collector.poller import poll_org
    tag = uuid.uuid4().hex[:6]
    since = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    repos = [{"full_name": f"zzorg-{tag}/r{i}", "archived": i == 1,
              "pushed_at": (since + timedelta(minutes=10 - i)).isoformat()} for i in range(5)]
    repos += [{"full_name": f"zzorg-{tag}/old{i}", "archived": False,
               "pushed_at": (since - timedelta(hours=i + 1)).isoformat()} for i in range(200)]
    calls = []

    def get(url, headers=None):
        calls.append(url)
        page = int(url.split("page=")[-1])
        return 200, repos[(page - 1) * 100: page * 100]
    try:
        out = poll_org(f"zzorg-{tag}", since, get=get)
        assert out["calls"] == 1, "stops at the first repo older than the last poll"
        assert out["changed"] == 4, "5 pushed since, minus 1 archived"
        with admin_conn() as conn:
            n = conn.execute("select count(*) from repo_state where repo like %s and dirty",
                             (f"zzorg-{tag}/%",)).fetchone()[0]
        assert n == 4
    finally:
        with admin_conn() as conn:
            conn.execute("delete from repo_state where repo like %s", (f"zzorg-{tag}/%",))


def test_daily_refresh_revisits_every_unit(mono):
    full, repo = mono
    head = Syncer(LocalGitSource({full: repo})).sync(full, None)["head"]
    daily = Syncer(LocalGitSource({full: repo}), daily=True)
    r = daily.sync(full, head)
    assert r["status"] == "processed" and r["changed_units"] == 3, \
        "the daily pass ignores fingerprints so contributors and stage refresh everywhere"
