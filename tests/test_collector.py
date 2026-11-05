"""The collection agent: triage, routing, the owner gate, attribution and stage.

Uses a tiny local repo built in tmp_path and stubbed GitHub lookups, against the real
Postgres store. Run: .venv/Scripts/python -m pytest -q tests/test_collector.py
"""

from __future__ import annotations

import uuid

import pytest

from orgmem.collector import ChangedFile, Deps, Event, build_collector, run_event
from orgmem.collector.stage import infer
from orgmem.db import admin_conn


def make_repo(root, name):
    r = root / name
    (r / "pkg").mkdir(parents=True)
    (r / "package.json").write_text('{"name": "%s", "description": "Retry HTTP calls with '
                                    'exponential backoff and jitter", "version": "1.4.0"}'
                                    % name)
    (r / "README.md").write_text("# %s\n\nRetries failed HTTP requests with exponential "
                                 "backoff and jitter for our services.\n" % name)
    (r / "retry.test.ts").write_text(
        "it('gives up after max attempts', () => {})\n"
        "it('rejects a negative delay', () => {})\n")
    return r


@pytest.fixture
def env(tmp_path):
    name = f"zz-test-retry-{uuid.uuid4().hex[:6]}"
    make_repo(tmp_path, name)
    deps = Deps(
        repos_roots=[tmp_path],
        repo_meta=lambda full: {"description": "Retry HTTP calls with backoff",
                                "archived": False},
        releases=lambda full: [{"tag": "v1.4.0", "prerelease": False}],
        contributors=lambda full, path: [
            {"login": "alice-test", "commits": 12, "first_at": "2026-01-01T00:00:00Z",
             "last_at": "2026-10-01T00:00:00Z"}],
    )
    graph = build_collector(deps)        # in-memory: no checkpointer needed for most tests
    yield name, deps, graph
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from asset where unit_id like %s", (f"github:{name}:%",))


def commit(name, subject, files, actor="alice-test"):
    return Event(source="github", kind="commit", object_ref=f"testorg/{name}",
                 version=uuid.uuid4().hex, actor=actor, title=subject, body=subject,
                 occurred_at="2026-11-01T10:00:00Z",
                 files=[ChangedFile(path=f, status=s) for f, s in files])


def test_triage_drops_bots_dependency_bumps_and_lockfiles(env):
    from langgraph.checkpoint.memory import InMemorySaver
    name, deps, _ = env
    g = build_collector(deps, checkpointer=InMemorySaver())
    cases = [
        commit(name, "Update retry", [("retry.ts", "modified")], actor="renovate[bot]"),
        commit(name, "chore(deps): bump axios from 1.6 to 1.7", [("package.json", "modified")]),
        commit(name, "regen", [("package-lock.json", "modified")]),
    ]
    for ev in cases:
        out = run_event(g, ev, uuid.uuid4().hex)
        assert out["status"] == "ignored", ev.title


def test_new_unit_asks_owner_about_visibility_then_stores_everything(env):
    from langgraph.checkpoint.memory import InMemorySaver
    name, deps, _ = env
    g = build_collector(deps, checkpointer=InMemorySaver())
    ev = commit(name, "Add retry helper", [("retry.ts", "added"),
                                            ("package.json", "added")])
    asked = []

    def owner(questions):
        asked.extend(questions)
        return {f"{q['unit_id']}:{q['ask']}":
                ("org-wide" if q["ask"] == "visibility" else "confirm") for q in questions}

    out = run_event(g, ev, uuid.uuid4().hex, owner_policy=owner)
    assert out["status"] == "applied"
    assert any(q["ask"] == "visibility" for q in asked), "a new asset must ask its owner"
    (uid, res), = out["result"].items()
    assert res["action"] == "create" and res["scope"] == "org-wide"
    assert res["stage"] == "prod", "stable v1.4.0 release should infer prod"
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("""select p.handle, t.name, l.name, m.display_name
                         from asset a join asset_contributor ac on ac.asset_id = a.id
                         join person p on p.id = ac.person_id
                         join team t on t.id = p.team_id join lob l on l.id = t.lob_id
                         left join person m on m.id = p.manager_id
                        where a.unit_id = %s""", (uid,))
        handle, team, lob, manager = cur.fetchone()
        cur.execute("select stage_confirmed from asset where unit_id = %s", (uid,))
        assert cur.fetchone()[0] is False, "an inferred stage is never auto-confirmed"
    assert handle.startswith("eng-") and "alice" not in handle, "people are pseudonymous"
    # A person belongs to ONE team and may contribute to other teams' assets, so the
    # contributor's team is not necessarily the asset's: it only has to be a real team
    # with an LOB and a manager. (The asset's own team is checked via asset.owner_team.)
    assert team.startswith("team-") and lob and manager.startswith("Lead,")
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select t.name from asset a join team t on t.id = a.owner_team "
                    "where a.unit_id = %s", (uid,))
        assert cur.fetchone()[0] == f"team-{name}"


def test_second_commit_updates_without_asking_again(env):
    from langgraph.checkpoint.memory import InMemorySaver
    name, deps, _ = env
    g = build_collector(deps, checkpointer=InMemorySaver())
    owner = lambda qs: {f"{q['unit_id']}:{q['ask']}": "team-only" for q in qs}  # noqa: E731
    run_event(g, commit(name, "Add retry", [("package.json", "added")]), "a" + uuid.uuid4().hex,
              owner_policy=owner)
    asked = []
    out = run_event(g, commit(name, "Fix jitter", [("retry.ts", "modified")]),
                    "b" + uuid.uuid4().hex,
                    owner_policy=lambda qs: asked.extend(qs) or {})
    assert out["status"] == "applied" and not asked
    (_, res), = out["result"].items()
    assert res["action"] == "update" and res["scope"] == "team-only"


def test_deprecation_is_never_applied_without_the_owner(env):
    from langgraph.checkpoint.memory import InMemorySaver
    name, deps, _ = env
    g = build_collector(deps, checkpointer=InMemorySaver())
    keep = lambda qs: {f"{q['unit_id']}:{q['ask']}":  # noqa: E731
                       ("team-only" if q["ask"] == "visibility" else "reject") for q in qs}
    run_event(g, commit(name, "Add retry", [("package.json", "added")]), uuid.uuid4().hex,
              owner_policy=keep)
    out = run_event(g, commit(name, "Deprecate retry helper, superseded by http-kit",
                              [("retry.ts", "modified")]), uuid.uuid4().hex,
                    owner_policy=keep)
    (_, res), = out["result"].items()
    assert res["stage"] != "deprecated", "owner rejected: must not be deprecated"


def test_unanswered_owner_question_leaves_the_event_pending(env):
    from langgraph.checkpoint.memory import InMemorySaver
    name, deps, _ = env
    g = build_collector(deps, checkpointer=InMemorySaver())
    out = run_event(g, commit(name, "Add retry", [("package.json", "added")]),
                    uuid.uuid4().hex, owner_policy=None)
    assert out["status"] == "pending_owner" and out["questions"]
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from asset where unit_id like %s", (f"github:{name}:%",))
        assert cur.fetchone()[0] == 0, "nothing is stored before the owner answers"


def test_doc_pages_only_stored_when_they_describe_a_solution(env):
    from langgraph.checkpoint.memory import InMemorySaver
    name, deps, _ = env
    g = build_collector(deps, checkpointer=InMemorySaver())
    notes = Event(source="confluence", kind="page_updated",
                  object_ref=f"confluence:ENG/{uuid.uuid4().hex[:6]}", version="3",
                  actor="bob", title="Weekly sync meeting notes",
                  body="Attendees: everyone. Agenda: roadmap. " * 20)
    assert run_event(g, notes, uuid.uuid4().hex)["status"] == "ignored"

    how = Event(source="confluence", kind="page_updated",
                object_ref=f"confluence:ENG/{uuid.uuid4().hex[:6]}", version="7",
                actor="bob", title="How we solved duplicate webhook deliveries",
                body=("We solved duplicate deliveries by keying every event on source, "
                      "object and version, so a redelivered webhook is a no-op. "
                      "Pitfall: GitHub does not redeliver failed webhooks, so a nightly "
                      "reconcile job is required. ") * 3)
    out = run_event(g, how, uuid.uuid4().hex,
                    owner_policy=lambda qs: {f"{q['unit_id']}:{q['ask']}": "team-only"
                                             for q in qs})
    try:
        assert out["status"] == "applied"
        (uid, res), = out["result"].items()
        assert uid.startswith("page:confluence:") and res["new_claims"] >= 1
    finally:
        with admin_conn() as conn, conn.cursor() as cur:
            cur.execute("delete from asset where unit_id like 'page:confluence:ENG/%%'")


def test_personnel_data_never_becomes_a_claim(env):
    from langgraph.checkpoint.memory import InMemorySaver
    name, deps, _ = env
    g = build_collector(deps, checkpointer=InMemorySaver())
    ev = commit(name, "Add retry; workaround for timeout, unassigning myself before "
                      "the vacation", [("package.json", "added")])
    out = run_event(g, ev, uuid.uuid4().hex,
                    owner_policy=lambda qs: {f"{q['unit_id']}:{q['ask']}": "team-only"
                                             for q in qs})
    (uid, _), = out["result"].items()
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from claim c join asset a on a.id=c.asset_id "
                    "where a.unit_id=%s and c.body ilike '%%vacation%%'", (uid,))
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("kw,expect", [
    (dict(archived=True), "retired"),
    (dict(releases=[{"tag": "v2.1.0", "prerelease": False}]), "prod"),
    (dict(releases=[{"tag": "v0.9.0", "prerelease": False}]), "beta"),
    (dict(releases=[{"tag": "v1.0.0-rc1", "prerelease": True}]), "beta"),
    (dict(readme="This is EXPERIMENTAL and may break."), "dev"),
    (dict(), "dev"),
])
def test_stage_inference(kw, expect):
    assert infer(**kw).stage == expect


def test_stage_flags_deprecation_for_the_owner_instead_of_applying_it():
    st = infer(releases=[{"tag": "v3.0.0", "prerelease": False}],
               change_text="Deprecate this package")
    assert st.needs_owner and st.proposed == "deprecated" and st.stage == "prod"
