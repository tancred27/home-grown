"""Answer traces: why an answer came out the way it did, step by step.

A trace records, for one question:
  * the request: question, keywords, the model's rewrite;
  * keyword search: each candidate's BM25 score broken down by term (term frequency in the
    asset's text and in its claims, document frequency, idf, contribution);
  * vector search: each candidate's two best-matching fields, their similarity and text;
  * fusion: each candidate's rank in each list and its fused score;
  * the judge: model, prompt version, each verdict and its reason, or the failure;
  * the answer: which candidates were shown and in what order;
  * where each candidate's text came from (unit -> code areas -> files at a commit, which
    model summarised and compiled it);
  * timings for every step.

Traces are stored in `query_trace` under row-level security: the person who asked can read
theirs; operators read them through the owner connection. They hold only assets the asker
could already see. Telemetry never carries any of this (its no-content rule stands).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

from .config import settings

TRACE_VERSION = 1


def new(question: str, keywords: list[str] | None) -> dict:
    return {"version": TRACE_VERSION, "question": question, "keywords": keywords or [],
            "timings_ms": {}, "candidates": {}}


class step:  # noqa: N801  (used as a context manager: `with explain.step(trace, "search"):`)
    """Times one step into trace["timings_ms"]; does nothing when trace is None."""

    def __init__(self, trace: dict | None, name: str):
        self.trace, self.name = trace, name

    def __enter__(self):
        self.t0 = time.monotonic()
        return self

    def __exit__(self, *exc):
        if self.trace is not None:
            self.trace["timings_ms"][self.name] = round((time.monotonic() - self.t0) * 1000, 1)
        return False


def prompt_version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def candidate(trace: dict, asset_id) -> dict:
    return trace["candidates"].setdefault(str(asset_id), {})


# ---------------------------------------------------------------- breakdowns (as the asker)

def keyword_breakdown(cur, trace: dict, tsq: str, ids: list) -> None:
    """Per-term BM25 contributions for the candidates (db/010 bm25_explain)."""
    if not tsq or not ids or settings.lexical != "bm25":
        return
    cur.execute(
        "select asset_id, term, tf_text, tf_claims, df, idf, score, doc_len, avgdl, n "
        "from bm25_explain(to_tsquery('english', %s), "
        "coalesce(tsvector_to_array(to_tsvector('english', %s)), '{}'), %s)",
        (tsq, tsq.replace("|", " "), list(ids)))
    for aid, term, tft, tfc, df, idf, sc, dl, avg, n in cur.fetchall():
        c = candidate(trace, aid)
        k = c.setdefault("keyword", {"terms": [], "doc_len": dl, "avg_doc_len": avg,
                                     "corpus_size": n})
        k["terms"].append({"term": term, "tf_text": tft, "tf_claims": tfc, "df": df,
                           "idf": round(idf, 4), "contribution": round(sc, 4)})
    for c in trace["candidates"].values():
        if "keyword" in c:
            c["keyword"]["terms"].sort(key=lambda t: -t["contribution"])


def vector_breakdown(cur, trace: dict, qvec, ids: list) -> None:
    """Each candidate's two best-matching fields, with similarity and the field's text."""
    if not ids:
        return
    cur.execute(
        "select asset_id, field, 1 - (vec <=> %s) as sim, text from embedding "
        "where model = %s and asset_id = any(%s) order by asset_id, vec <=> %s",
        (qvec, settings.embed_model, list(ids), qvec))
    for aid, field, sim, text in cur.fetchall():
        best = candidate(trace, aid).setdefault("vector", {"best_fields": []})["best_fields"]
        if len(best) < 2:
            best.append({"field": field, "similarity": round(float(sim), 4),
                         "text": (text or "")[:400] or None})


def provenance(cur, trace: dict, ids: list) -> None:
    """Where each candidate came from: its repo, profile and code areas (crawler units)."""
    if not ids:
        return
    cur.execute(
        "select a.id, a.kind, a.unit_id, p.name, pr.model, pr.prompt_version, pr.commit_sha, "
        "pr.areas, pr.updated_at from asset a left join asset p on p.id = a.parent_id "
        "left join profile pr on pr.asset_id = a.id where a.id = any(%s)", (list(ids),))
    for aid, kind, unit_id, parent, model, pv, sha, areas, at in cur.fetchall():
        src = {"kind": kind, "unit_id": unit_id, "repo": parent}
        if model:
            src["profile"] = {"model": model, "prompt_version": pv, "commit": sha,
                              "updated_at": at.isoformat() if at else None}
            cur.execute("select path, files, model, commit_sha from area where asset_id = %s "
                        "order by path", (aid,))
            src["areas"] = [{"path": p, "files": list(f or [])[:10], "summary_model": m,
                             "commit": c} for p, f, m, c in cur.fetchall()]
            src["areas_listed"] = list(areas or [])
        candidate(trace, aid)["source"] = src


# ---------------------------------------------------------------- storage

def save(user_id, intent: str, keywords: list[str], route: str, verdicts,
         trace: dict | None) -> str | None:
    """Write the question row (and its trace, unless tracing is off) as the asker.
    Returns the question id, or None if it could not be written. Never raises."""
    from .db import as_user
    try:
        with as_user(user_id) as conn, conn.cursor() as cur:
            cur.execute(
                "insert into question (asker_id, intent, keywords, route, results) "
                "values (%s, %s, %s, %s, %s) returning id",
                (user_id, intent, keywords, route if route in ("cli", "mcp", "ui", "hook", "test")
                 else "ui", json.dumps([{"asset_id": v.asset_id, "verdict": v.verdict}
                                        for v in verdicts])))
            qid = cur.fetchone()[0]
            if trace is not None and settings.trace_enabled:
                trace["question_id"] = str(qid)
                trace["route"] = route
                cur.execute("insert into query_trace (question_id, asker_id, trace) "
                            "values (%s, %s, %s)", (qid, user_id, json.dumps(trace, default=str)))
            conn.commit()
            return str(qid)
    except Exception:
        return None


def load(user_id, question_id: str) -> dict | None:
    """A trace, read as the asker (row-level security: your own questions only)."""
    from .db import as_user
    try:
        uuid.UUID(str(question_id))
    except ValueError:
        return None
    with as_user(user_id) as conn:
        row = conn.execute("select trace from query_trace where question_id = %s",
                           (question_id,)).fetchone()
    return row[0] if row else None


def prune(days: int | None = None) -> int:
    """Remove traces older than the retention period (operators run this on a schedule).
    Question rows stay; only their traces go."""
    from .db import admin_conn
    days = settings.trace_retention_days if days is None else days
    with admin_conn() as conn:
        n = conn.execute("delete from query_trace "
                         "where created_at < now() - make_interval(days => %s)",
                         (days,)).rowcount
    return n
