"""The code-reading crawler end to end against the database, with every model call stubbed."""

from __future__ import annotations

import json
import uuid

import pytest

from homegrown import llm
from homegrown.collector import crawl
from homegrown.config import settings
from homegrown.db import admin_conn

FILES = {
    "README.md": "# payments\nRefunds and invoices for the shop.",
    "package.json": '{"name": "payments"}',
    "src/refunds/api/refund.ts": (
        "export function refundOrder(orderId: string, amount: number) {}\n"
        "/** Partial refunds with an audit trail. */\nexport class RefundService {}\n"
        + "\n".join(f"export function helper{i}(x: number): number" for i in range(30))),
    "src/invoices/pdf/render.ts": (
        "export function renderInvoicePdf(invoiceId: string) {}\n"
        "const API_KEY = '" + "sk-" + "live-abcdefghijklmnopqrstuvwxyz123456'\n"
        + "\n".join(f"export function layout{i}(page: number): void" for i in range(30))),
    "src/refunds/api/refund.test.ts": "it('refunds', () => {})",
}


@pytest.fixture
def repo(monkeypatch):
    tag = uuid.uuid4().hex[:8]
    name = f"crawlrepo-{tag}"
    full = f"test-org/{name}"
    with admin_conn() as conn:
        team = conn.execute("insert into team (name) values (%s) returning id",
                            (f"team-crawl-{tag}",)).fetchone()[0]
        root = conn.execute(
            "insert into asset (kind, name, source_ref, unit_id, owner_team, discovery_scope, "
            "summary) values ('library', %s, %s, %s, %s, %s, 'Refunds and invoices') returning id",
            (name, f"github:{full}", f"github:{name}:.", team, team)).fetchone()[0]
    calls = {"area": [], "units": [], "fail_units": False}

    def fake(system, user, schema, max_tokens=2000, model=None):
        assert "sk-live-" not in user, "a secret reached the model"
        if system == crawl.AREA_SYS:
            calls["area"].append((model, user.splitlines()[0]))
            area = user.splitlines()[0].removeprefix("Repository area: ")
            # The summary follows the code, as a real one would.
            return {"summary": f"Provides the {area} code ({len(user)} chars of declarations)."}
        calls["units"].append((model, json.loads(user)))
        if calls["fail_units"]:
            raise llm.LLMUnavailable("down")
        areas = [a["area"] for a in json.loads(user)["code_areas"]]
        return {"units": [
            {"name": "Refund API", "unit_type": "service", "purpose": "Issue partial refunds.",
             "capabilities": ["partial refunds", "refund audit trail"], "search_terms": ["refund"],
             "example_questions": ["How do I refund part of an order?"], "distinct": "TypeScript",
             "areas": [a for a in areas if "refund" in a]},
            {"name": "Invoice PDF", "unit_type": "library", "purpose": "Render invoices as PDF.",
             "capabilities": ["invoice PDF rendering"], "search_terms": ["invoice", "pdf"],
             "example_questions": ["How do I render an invoice PDF?"], "distinct": "PDF",
             "areas": [a for a in areas if "invoice" in a]}]}
    monkeypatch.setattr(llm, "structured", fake)
    monkeypatch.setattr(settings, "crawl_area_vectors", 5)
    files = dict(FILES)
    blobs = {p: uuid.uuid4().hex for p in files}
    yield {"full": full, "root": root, "calls": calls, "files": files, "blobs": blobs, "team": team}
    with admin_conn() as conn:
        ids = [r[0] for r in conn.execute("select id from asset where parent_id = %s", (root,))]
        conn.execute("delete from area where repo = %s", (full,))
        conn.execute("delete from unit_fingerprint where unit_id = %s", (f"crawl:{full}",))
        conn.execute("delete from asset where id = any(%s)", (ids + [root],))
        conn.execute("delete from team where id = %s", (team,))


def _run(r, force=False):
    by_sha = {s: r["files"][p] for p, s in r["blobs"].items()}
    return crawl.crawl_repo(r["full"], "c0ffee", r["blobs"], lambda p, s: by_sha[s], force=force)


def test_first_crawl_writes_areas_units_profiles_claims_and_vectors(repo):
    st = _run(repo)
    assert st["status"] == "crawled" and st["units"] == 2 and st["summarised"] == 2
    assert {m for m, _ in repo["calls"]["area"]} == {settings.crawl_summary_model}
    assert repo["calls"]["units"][0][0] == settings.crawl_compile_model
    with admin_conn() as conn:
        units = conn.execute(
            "select a.id, a.name, a.kind, a.discovery_scope, p.areas from asset a "
            "join profile p on p.asset_id = a.id where a.parent_id = %s order by a.name",
            (repo["root"],)).fetchall()
        assert [(n, k) for _, n, k, _, _ in units] == [("Invoice PDF", "library"),
                                                       ("Refund API", "service")]
        assert all(s == repo["team"] for _, _, _, s, _ in units)
        refund = units[1][0]
        claims = conn.execute(
            "select c.body, e.source, e.source_version from claim c join evidence e "
            "on e.id = c.evidence_id where c.asset_id = %s and c.valid_to is null",
            (refund,)).fetchall()
        assert {b for b, _, _ in claims} == {"partial refunds", "refund audit trail"}
        assert all(src == "github_code" and v == "c0ffee" for _, src, v in claims)
        fields = {f for (f,) in conn.execute("select field from embedding where asset_id = %s",
                                             (refund,))}
        assert {"card", "purpose", "capabilities", "search_terms", "question_1", "area_1"} <= fields
        area_text = conn.execute("select string_agg(coalesce(summary,''), ' ') from area "
                                 "where repo = %s", (repo["full"],)).fetchone()[0]
        assert "sk-live-" not in area_text


def test_unchanged_repo_is_skipped(repo):
    _run(repo)
    n_area, n_units = len(repo["calls"]["area"]), len(repo["calls"]["units"])
    assert _run(repo)["status"] == "skipped"
    assert (len(repo["calls"]["area"]), len(repo["calls"]["units"])) == (n_area, n_units)


def test_code_change_resummarises_only_that_area_and_keeps_unit_ids(repo):
    _run(repo)
    with admin_conn() as conn:
        before = dict(conn.execute("select name, unit_id from asset where parent_id = %s",
                                   (repo["root"],)).fetchall())
    n_area = len(repo["calls"]["area"])
    repo["files"]["src/refunds/api/refund.ts"] += "\nexport function refundShipping(id: string) {}"
    repo["blobs"]["src/refunds/api/refund.ts"] = uuid.uuid4().hex
    assert _run(repo)["status"] == "crawled"
    assert len(repo["calls"]["area"]) == n_area + 1             # only the changed area
    with admin_conn() as conn:
        after = dict(conn.execute("select name, unit_id from asset where parent_id = %s",
                                  (repo["root"],)).fetchall())
    assert after == before


def test_failed_compile_keeps_previous_units(repo):
    _run(repo)
    repo["calls"]["fail_units"] = True
    repo["files"]["src/invoices/pdf/render.ts"] += "\nexport function renderCreditNote() {}"
    repo["blobs"]["src/invoices/pdf/render.ts"] = uuid.uuid4().hex
    assert _run(repo)["status"] == "compile-failed"
    with admin_conn() as conn:
        n = conn.execute("select count(*) from profile p join asset a on a.id = p.asset_id "
                         "where a.parent_id = %s", (repo["root"],)).fetchone()[0]
    assert n == 2


def test_normalise_assigns_every_area_once():
    units = [{"name": "A", "unit_type": "weird", "purpose": "p", "capabilities": [],
              "search_terms": [], "example_questions": [], "distinct": "", "areas": ["x/a", "x/b"]},
             {"name": "B", "unit_type": "cli", "purpose": "p", "capabilities": [],
              "search_terms": [], "example_questions": [], "distinct": "", "areas": ["x/b", "y"]}]
    out = crawl.normalise_units(units, ["x/a", "x/b", "y", "x/c"])
    assert out[0]["unit_type"] == "library" and out[1]["unit_type"] == "cli"
    flat = [a for u in out for a in u["areas"]]
    assert sorted(flat) == ["x/a", "x/b", "x/c", "y"] and "x/c" in out[0]["areas"]


def test_match_ids_by_area_overlap():
    prev = [{"unit_id": "u1", "areas": ["a", "b"]}, {"unit_id": "u2", "areas": ["c"]}]
    new = [{"areas": ["c", "d"]}, {"areas": ["a", "b", "e"]}, {"areas": ["z"]}]
    assert crawl.match_ids(new, prev) == ["u2", "u1", None]


def test_unit_keyword_text_includes_profile_search_terms(repo):
    _run(repo)
    with admin_conn() as conn:
        hit = conn.execute(
            "select a.name from asset a where a.parent_id = %s "
            "and a.tsv @@ plainto_tsquery('english', 'render invoice pdf')",
            (repo["root"],)).fetchall()
    assert ("Invoice PDF",) in hit


def test_failed_summaries_are_retried_on_the_next_pass(repo, monkeypatch):
    real = llm.structured

    def no_model(*a, **k):
        raise llm.LLMUnavailable("no key")
    monkeypatch.setattr(llm, "structured", no_model)
    assert _run(repo)["status"] == "no-summaries"
    monkeypatch.setattr(llm, "structured", real)
    st = _run(repo)                       # same code, no force: must not be skipped
    assert st["status"] == "crawled" and st["summarised"] == 2


def test_repo_without_a_manifest_gets_its_own_asset(monkeypatch):
    """A repo the collector found no manifest in (no root asset) is still crawled."""
    tag = uuid.uuid4().hex[:8]
    full = f"test-org/nomanifest-{tag}"
    files = {"README.md": "# Signing tool\nSigns payloads.",
             "src/sign/core/signer.cs": "\n".join(
                 f"public class Signer{i} {{ public byte[] Sign(byte[] d) }}" for i in range(30))}
    blobs = {p: uuid.uuid4().hex for p in files}
    by_sha = {s: files[p] for p, s in blobs.items()}

    def fake(system, user, schema, max_tokens=2000, model=None):
        if system == crawl.AREA_SYS:
            return {"summary": "Signs byte payloads."}
        areas = [a["area"] for a in json.loads(user)["code_areas"]]
        return {"units": [{"name": "Payload signer", "unit_type": "library", "purpose": "Sign.",
                           "capabilities": ["signing"], "search_terms": [],
                           "example_questions": [], "distinct": "", "areas": areas}]}
    monkeypatch.setattr(llm, "structured", fake)
    try:
        st = crawl.crawl_repo(full, "c1", blobs, lambda p, s: by_sha[s])
        assert st["status"] == "crawled"
        with admin_conn() as conn:
            kind, summary = conn.execute("select kind, summary from asset where unit_id = %s",
                                         (f"github:nomanifest-{tag}:.",)).fetchone()
        assert kind == "repo" and "Signs payloads" in summary
    finally:
        with admin_conn() as conn:
            root = conn.execute("select id, owner_team from asset where unit_id = %s",
                                (f"github:nomanifest-{tag}:.",)).fetchone()
            if root:
                conn.execute("delete from asset where parent_id = %s", (root[0],))
                conn.execute("delete from asset where id = %s", (root[0],))
            conn.execute("delete from area where repo = %s", (full,))
            conn.execute("delete from unit_fingerprint where unit_id = %s", (f"crawl:{full}",))
