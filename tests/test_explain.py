"""Answer traces: what they record, who can read them, and that they leak nothing."""

from __future__ import annotations

import json
import uuid

import pytest

from homegrown import explain, llm
from homegrown.collector import crawl
from homegrown.config import settings
from homegrown.db import admin_conn
from homegrown.search import find_existing

DECLS = "\n".join(f"export function renderInvoicePage{i}(page: number): void" for i in range(30))


@pytest.fixture
def world(monkeypatch):
    """A crawled repo in team A, a user in A, an outsider in B, and B's hidden asset."""
    tag = uuid.uuid4().hex[:8]
    name = f"explainrepo-{tag}"
    full = f"test-org/{name}"
    word = f"zzinvoice{tag}"
    with admin_conn() as conn:
        ta, tb = (conn.execute("insert into team (name) values (%s) returning id",
                               (f"team-explain-{k}-{tag}",)).fetchone()[0] for k in "ab")
        users = {}
        for k, t in (("asker", ta), ("outsider", tb)):
            uid = conn.execute("insert into app_user (login, team_id) values (%s,%s) returning id",
                               (f"explain-{k}-{tag}", t)).fetchone()[0]
            conn.execute("insert into membership (user_id, group_id) values (%s,%s)", (uid, t))
            users[k] = uid
        root = conn.execute(
            "insert into asset (kind, name, source_ref, unit_id, owner_team, discovery_scope, "
            "summary) values ('library', %s, %s, %s, %s, %s, 'Invoices') returning id",
            (name, f"github:{full}", f"github:{name}:.", ta, ta)).fetchone()[0]
        hidden = conn.execute(
            "insert into asset (kind, name, source_ref, unit_id, owner_team, discovery_scope, "
            "summary) values ('library', %s, %s, %s, %s, %s, %s) returning id",
            (f"hidden-{tag}", f"github:hidden-{tag}", f"github:hidden-{tag}:.", tb, tb,
             f"{word} renders {word} pages")).fetchone()[0]

    def fake(system, user, schema, max_tokens=2000, model=None):
        if system == crawl.AREA_SYS:
            return {"summary": f"Renders {word} invoices to PDF pages."}
        areas = [a["area"] for a in json.loads(user)["code_areas"]]
        return {"units": [{"name": "Invoice renderer", "unit_type": "library",
                           "purpose": f"Render {word} invoices as PDF documents.",
                           "capabilities": [f"{word} PDF rendering", "page layout"],
                           "search_terms": ["invoice", "pdf"],
                           "example_questions": [f"How do I render a {word} as PDF?"],
                           "distinct": "TypeScript", "areas": areas}]}
    monkeypatch.setattr(llm, "structured", fake)
    blobs = {"README.md": "r1", "src/invoice/pdf/render.ts": "b1"}
    text = {"r1": "# invoices", "b1": DECLS}
    assert crawl.crawl_repo(full, "abc123", blobs, lambda p, s: text[s])["status"] == "crawled"
    yield {"users": users, "word": word, "root": root, "hidden": hidden, "full": full,
           "tag": tag}
    with admin_conn() as conn:
        conn.execute("delete from question where asker_id = any(%s)", (list(users.values()),))
        units = [r[0] for r in conn.execute("select id from asset where parent_id = %s", (root,))]
        conn.execute("delete from area where repo = %s", (full,))
        conn.execute("delete from unit_fingerprint where unit_id = %s", (f"crawl:{full}",))
        conn.execute("delete from asset where id = any(%s)", (units + [root, hidden],))
        conn.execute("delete from membership where user_id = any(%s)", (list(users.values()),))
        conn.execute("delete from app_user where id = any(%s)", (list(users.values()),))
        conn.execute("delete from team where id = any(%s)", ([ta, tb],))


def _ask(world, who="asker"):
    trace = explain.new(f"render a {world['word']} as a PDF", [])
    vs = find_existing(world["users"][who], trace["question"], [], trace=trace)
    qid = explain.save(world["users"][who], trace["question"], [], "test", vs, trace)
    return vs, trace, qid


def test_trace_records_every_step_for_the_found_unit(world):
    vs, trace, qid = _ask(world)
    assert qid and vs
    with admin_conn() as conn:
        unit = conn.execute("select id from asset where parent_id = %s",
                            (world["root"],)).fetchone()[0]
    c = trace["candidates"][str(unit)]
    assert c["fused_rank"] >= 1 and c["keyword_rank"] and c["vector_rank"]
    terms = {t["term"] for t in c["keyword"]["terms"]}
    assert world["word"] in terms and all(t["contribution"] >= 0 for t in c["keyword"]["terms"])
    best = c["vector"]["best_fields"]
    assert len(best) == 2 and all(b["text"] for b in best)
    assert c["source"]["profile"]["commit"] == "abc123"
    assert c["source"]["areas"][0]["path"] == "src/invoice/pdf"
    assert c["judge"]["verdict"] and trace["judge"]["mode"] == "rules"
    assert {"embed", "keyword_search", "vector_search", "breakdowns"} <= set(trace["timings_ms"])
    assert trace["answer"][0]["position"] == 1


def test_only_the_asker_can_read_a_trace(world):
    _, _, qid = _ask(world)
    got = explain.load(world["users"]["asker"], qid)
    assert got and got["question_id"] == qid
    assert explain.load(world["users"]["outsider"], qid) is None
    assert explain.load(None, qid) is None


def test_a_trace_never_mentions_an_asset_the_asker_cannot_see(world):
    _, trace, _ = _ask(world)
    assert str(world["hidden"]) not in json.dumps(trace)
    _, outsider_trace, _ = _ask(world, "outsider")
    assert str(world["root"]) not in json.dumps(outsider_trace)


def test_tracing_can_be_turned_off_and_pruned(world, monkeypatch):
    _, _, qid = _ask(world)
    assert explain.prune(days=0) >= 1
    assert explain.load(world["users"]["asker"], qid) is None
    monkeypatch.setattr(settings, "trace_enabled", False)
    _, _, qid2 = _ask(world)
    assert qid2 and explain.load(world["users"]["asker"], qid2) is None


def test_ask_names_the_units_repo_and_returns_an_explainable_question_id(world):
    from homegrown.answer import ask
    out = ask(world["users"]["asker"], f"render a {world['word']} as a PDF", route="test")
    assert out["question_id"]
    unit = next(m for m in out["matches"] if m["name"] == "Invoice renderer")
    assert unit["repo"] and unit["repo"] in out["text"]
    trace = explain.load(world["users"]["asker"], out["question_id"])
    assert trace["answer_label"] == out["answer"]
    assert any(s["asset_id"] == unit["asset_id"] for s in trace["shown"])


def test_report_says_where_an_expected_answer_was_lost(world):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "explain_report", Path(__file__).parent.parent / "scripts" / "explain_report.py")
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    _, trace, _ = _ask(world)
    with admin_conn() as conn:
        unit = str(conn.execute("select id from asset where parent_id = %s",
                                (world["root"],)).fetchone()[0])
    trace["shown"] = trace["answer"]
    assert report.classify(trace, unit)[0] == "found"
    assert report.classify(trace, None)[0] == "not indexed"
    assert report.classify(trace, str(uuid.uuid4()))[0] == "not retrieved"
