"""The answering agent, the webhook intake, and the new tables' security."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from orgmem.collector import ChangedFile, Deps, Event, build_collector, run_event
from orgmem.collector import adapters as A
from orgmem.db import admin_conn, as_user
from tests.test_collector import make_repo


# ---------------------------------------------------------------- webhooks

def test_github_signature_verification():
    body = b'{"x": 1}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert A.verify_github_signature("s3cret", body, good)
    assert not A.verify_github_signature("s3cret", body + b" ", good)
    assert not A.verify_github_signature("", body, good), "no secret configured -> reject"
    assert not A.verify_github_signature("s3cret", body, None)


def test_github_push_becomes_one_event_per_commit():
    payload = {"repository": {"full_name": "acme/retry"}, "commits": [
        {"id": "a1", "message": "Add retry\n\nlonger body", "timestamp": "t",
         "author": {"username": "dev1"}, "added": ["retry.ts"], "modified": [],
         "removed": []},
        {"id": "b2", "message": "Fix", "timestamp": "t", "author": {"username": "dev2"},
         "added": [], "modified": ["retry.ts"], "removed": ["old.ts"]}]}
    evs = A.from_github_push(payload)
    assert [e.version for e in evs] == ["a1", "b2"]
    assert evs[0].idem_key == "github:acme/retry:a1" and evs[0].title == "Add retry"
    assert {f.status for f in evs[1].files} == {"modified", "removed"}


def test_confluence_and_notion_map_to_the_same_envelope():
    c = A.from_confluence_webhook({"webhookEvent": "page_created", "user": {"accountId": "u1"},
                                   "page": {"id": "99", "spaceKey": "ENG", "title": "T",
                                            "version": {"number": 4},
                                            "body": {"storage": {"value": "<p>Hi</p>"}}}})
    assert (c.kind, c.object_ref, c.version, c.body) == \
        ("page_created", "confluence:ENG/99", "4", "Hi")
    n = A.from_notion_webhook({"entity": {"id": "abc", "title": "T", "text": "body"},
                               "timestamp": "2026-11-01", "authors": [{"id": "u2"}]})
    assert (n.source, n.object_ref, n.actor) == ("notion", "notion:abc", "u2")


def test_webhook_endpoint_rejects_unsigned_and_enqueues_signed(monkeypatch):
    from fastapi.testclient import TestClient

    from orgmem import api
    monkeypatch.setattr(api.settings, "github_webhook_secret", "s3cret")
    client = TestClient(api.app)
    sha = uuid.uuid4().hex
    body = json.dumps({"repository": {"full_name": "acme/zz-hook-test"}, "commits": [
        {"id": sha, "message": "x", "timestamp": "t", "author": {"username": "d"},
         "added": [], "modified": ["a.py"], "removed": []}]}).encode()
    assert client.post("/hooks/github", content=body,
                       headers={"X-GitHub-Event": "push"}).status_code == 401
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    h = {"X-GitHub-Event": "push", "X-Hub-Signature-256": sig}
    try:
        r1 = client.post("/hooks/github", content=body, headers=h).json()
        r2 = client.post("/hooks/github", content=body, headers=h).json()
        assert r1["queued"] == 1 and r2["queued"] == 0, "redelivery must be a no-op"
    finally:
        with admin_conn() as conn, conn.cursor() as cur:
            cur.execute("delete from event where idem_key = %s",
                        (f"github:acme/zz-hook-test:{sha}",))


# ---------------------------------------------------------------- security of new tables

def test_app_role_cannot_read_login_mapping():
    with as_user(None) as conn, conn.cursor() as cur:
        with pytest.raises(Exception):
            cur.execute("select count(*) from source_identity")


def test_directory_hidden_from_unidentified_callers():
    with as_user(None) as conn, conn.cursor() as cur:
        for t in ("person", "lob", "asset_contributor"):
            cur.execute(f"select count(*) from {t}")
            assert cur.fetchone()[0] == 0, f"{t} visible to an unidentified caller"


# ---------------------------------------------------------------- the answering agent

@pytest.fixture
def two_users_one_asset(tmp_path):
    """Asset X belongs to team T and stays team-only. Alice is in T; Bob is not."""
    name = f"zz-ans-retry-{uuid.uuid4().hex[:6]}"
    make_repo(tmp_path, name)
    deps = Deps(repos_roots=[tmp_path],
                repo_meta=lambda f: {"description": "Retry HTTP calls with backoff"},
                releases=lambda f: [{"tag": "v1.4.0", "prerelease": False}],
                contributors=lambda f, p: [{"login": "alice-ans", "commits": 9,
                                            "first_at": "2026-01-01T00:00:00Z",
                                            "last_at": "2026-10-01T00:00:00Z"}],
                owner_gate=True)        # the owner keeps X team-only
    g = build_collector(deps, checkpointer=InMemorySaver())
    ev = Event(source="github", kind="commit", object_ref=f"testorg/{name}",
               version=uuid.uuid4().hex, actor="alice-ans", title="Add retry",
               files=[ChangedFile(path="package.json", status="added")])
    run_event(g, ev, uuid.uuid4().hex,
              owner_policy=lambda qs: {f"{q['unit_id']}:{q['ask']}": "team-only"
                                       for q in qs})
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from team where name = %s", (f"team-{name}",))
        team = cur.fetchone()[0]
        ids = {}
        for login, member in (("zz-alice", True), ("zz-bob", False)):
            cur.execute("insert into app_user (login) values (%s) on conflict (login) "
                        "do update set login = excluded.login returning id", (login,))
            ids[login] = cur.fetchone()[0]
            if member:
                cur.execute("insert into membership values (%s,%s) on conflict do nothing",
                            (ids[login], team))
    yield name, ids
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from asset where unit_id like %s", (f"github:{name}:%",))
        cur.execute("delete from membership where user_id = any(%s)", (list(ids.values()),))
        cur.execute("delete from app_user where id = any(%s)", (list(ids.values()),))


def test_answer_says_who_built_it_their_team_lob_manager_and_stage(two_users_one_asset):
    from orgmem.answer import ask
    name, ids = two_users_one_asset
    out = ask(ids["zz-alice"], "retry failed http calls with exponential backoff")
    mine = [m for m in out["matches"] if m["owner_team"] == f"team-{name}"]
    assert mine, f"expected the team's own asset in the answer: {out['text']}"
    m = mine[0]
    assert out["answer"] == "possibly", "rule verdicts must never claim a plain 'yes'"
    assert m["stage"]["stage"] == "prod" and m["stage"]["confirmed_by_owner"] is False
    assert m["lob"] and m["team_manager"].startswith("Lead,")
    assert m["built_by"] and m["built_by"][0]["manager"].startswith("Lead,")
    assert m["built_by"][0]["name"].startswith("Engineer "), "pseudonymous"


def test_answer_reveals_nothing_about_an_asset_the_asker_cannot_discover(
        two_users_one_asset):
    from orgmem.answer import ask
    name, ids = two_users_one_asset
    out = ask(ids["zz-bob"], "retry failed http calls with exponential backoff")
    assert not [m for m in out["matches"] if m["owner_team"] == f"team-{name}"]
    assert name not in out["text"]
    with as_user(ids["zz-bob"]) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from asset_contributor ac join person p "
                    "on p.id = ac.person_id join team t on t.id = p.team_id "
                    "where t.name = %s", (f"team-{name}",))
        assert cur.fetchone()[0] == 0, "contributors of an invisible asset leaked"


def test_sibling_units_are_told_apart_by_their_path():
    from orgmem.answer.graph import _unit_path
    assert _unit_path("github:github-rate-limits-prometheus-exporter:.") == ""
    assert _unit_path("github:github-rate-limits-prometheus-exporter:"
                      "helm/github-rate-limits-prometheus-exporter") == \
        "helm/github-rate-limits-prometheus-exporter"
    assert _unit_path("page:docs:grafana/x/docs/a.md") == "grafana/x/docs/a.md"
    assert _unit_path(None) == ""
