"""The read path: hybrid search, then a verdict. See docs/design.md section 7.

Everything here runs as the asking user under row-level security. The caller cannot
widen their own visibility, because the filter is a database policy rather than a
WHERE clause this module remembers to add.

Vector search is an EXACT SCAN, never an approximate index. Measured: HNSW under RLS
gave recall 0.42 and returned nothing at all for 56 of 100 users, silently
(experiments/RESULTS.md run B). An index that quietly returns an empty list is worse
than a slow scan.
"""

from __future__ import annotations

import re
import uuid

import psycopg

from .embed import embed_query
from .models import Verdict

# Stop words are dropped before building the tsquery; Postgres would drop them anyway,
# but removing them here keeps the OR query from being all noise.
_STOP = frozenset("""a an the and or but if then than that this these those is are was
were be been being to of in on at by for with from as it its into over under about can
could will would should may might must do does did done have has had so such no not
only own same too very just now we i you they our your their them us me my what which
who when where why how all any both each few more most other some want need get got
make made use used using write writing new several""".split())


def _tsquery_or(text: str) -> str:
    """Build an OR'd tsquery from free text.

    plainto_tsquery() ANDs every term, so a sentence-length intent like "join several
    running processes into a ring" requires all eight words to appear and matches
    nothing. That made the lexical half of this search contribute exactly zero on all
    51 gate-1 queries while the vector half silently carried the result -- the kind of
    failure that looks like a working system until something measures it.
    """
    words = [w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in _STOP]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return " | ".join(seen[:40])

# pgvector will happily use an HNSW index here and silently lose most rows once RLS
# narrows the visible set. Disabling index scans for this statement forces the exact
# scan the design requires. If latency ever makes this untenable, the documented
# fallback is pgvector 0.8 iterative scans -- not re-enabling the plain index.
_FORCE_EXACT = "set local enable_indexscan = off; set local enable_bitmapscan = off;"


def search(conn: psycopg.Connection, intent: str, keywords: list[str] | None = None,
           limit: int = 50) -> list[dict]:
    """Full-text and vector search in one pass, fused by reciprocal rank.

    `keywords` exists because agent-written synonyms moved retrieval quality more than
    anything else we tested -- the caller usually knows alternative names we do not.
    """
    terms = " ".join([intent, *(keywords or [])])
    qvec = embed_query(terms)
    tsq = _tsquery_or(terms)

    with conn.cursor() as cur:
        cur.execute(_FORCE_EXACT)

        # Lexical: asset name/aliases/summary, plus claim bodies, since the useful
        # query is often about behaviour rather than about a package name.
        lexical: list[tuple] = []
        if tsq:
            cur.execute(
                """
                select a.id, a.name,
                       max(ts_rank(a.tsv, to_tsquery('english', %s))) as arank,
                       max(coalesce(ts_rank(to_tsvector('english', c.body),
                                            to_tsquery('english', %s)), 0)) as crank
                  from asset a
                  left join claim c on c.asset_id = a.id and c.valid_to is null
                 where a.tsv @@ to_tsquery('english', %s)
                    or to_tsvector('english', coalesce(c.body,'')) @@ to_tsquery('english', %s)
                 group by a.id, a.name
                 order by 3 desc, 4 desc
                 limit %s
                """,
                (tsq, tsq, tsq, tsq, limit),
            )
            lexical = [(r[0], r[1], float(r[2]) + 0.5 * float(r[3])) for r in cur.fetchall()]

        cur.execute(
            """
            select a.id, a.name, 1 - (e.vec <=> %s) as sim
              from embedding e
              join asset a on a.id = e.asset_id
             order by e.vec <=> %s
             limit %s
            """,
            (qvec, qvec, limit),
        )
        semantic = [(r[0], r[1], float(r[2])) for r in cur.fetchall()]

    # Fusion by MAX of reciprocal ranks, not sum.
    #
    # Summing (textbook RRF) measurably hurt on the gate-1 set: hybrid recall@5 fell
    # to 0.61 against 0.71 for semantic alone on low-overlap queries, and one gold
    # asset ranked #1 by vector search was pushed to #11. With K=60 and 20 candidates
    # every reciprocal rank sits between 1/61 and 1/81, so "appears in both lists"
    # almost always outscores "ranked first by the better list" -- which is the wrong
    # trade when the two retrievers are not equally good, and here they are not.
    #
    # Max-fusion takes the best evidence for a candidate instead of rewarding
    # agreement, so a strong single signal survives. Chosen after seeing the failure,
    # which means the post-fix number on this same query set is optimistic; it needs a
    # held-out set to confirm.
    K = 60.0
    fused: dict[uuid.UUID, dict] = {}

    def _put(aid, name, rank, which, raw):
        row = fused.setdefault(aid, {"asset_id": aid, "name": name, "score": 0.0,
                                     "lexical": None, "semantic": None})
        row["score"] = max(row["score"], 1.0 / (K + rank + 1))
        row[which] = raw

    for rank, (aid, name, score) in enumerate(lexical):
        _put(aid, name, rank, "lexical", score)
    for rank, (aid, name, score) in enumerate(semantic):
        _put(aid, name, rank, "semantic", score)

    # Tie-break on having both signals, which recovers the complementarity that
    # max-fusion gives up without letting it override a strong single signal.
    return sorted(
        fused.values(),
        key=lambda r: (-r["score"], -(r["lexical"] is not None and r["semantic"] is not None)),
    )


def load_claims(conn: psycopg.Connection, asset_id) -> list[dict]:
    """Claims the caller may see. Sensitive ones are filtered by policy, not here."""
    with conn.cursor() as cur:
        cur.execute(
            "select id, layer, kind, body, confidence from claim "
            "where asset_id = %s and valid_to is null order by layer, kind",
            (asset_id,))
        return [{"claim_id": str(r[0]), "layer": r[1], "kind": r[2],
                 "body": r[3], "confidence": r[4]} for r in cur.fetchall()]


def judge(intent: str, name: str, claims: list[dict], score: float) -> Verdict:
    """Rule-based verdict, standing in for the model call.

    Deliberately conservative, and the layer weighting is the design position from
    docs/design.md section 7: layer 2 (tests) is authoritative about capability
    because CI re-verifies it; layer 3 is authoritative about fit in practice. A
    candidate with tests but no experience may be recommended on capability, but the
    response must say nobody is known to have used it.

    A wrong "use as-is" costs more than a rebuild, so thin evidence returns `unknown`
    with the gap named rather than a guess. Replacing this with a model is gated on
    claim accuracy (docs/design.md section 9); until then the rules keep the whole
    read path runnable and testable.
    """
    l2 = [c for c in claims if c["layer"] == 2]
    l3 = [c for c in claims if c["layer"] == 3]
    refusals = [c for c in l2 if c["kind"] == "refusal"]
    limits = [c for c in l2 if c["kind"] in ("limit", "scale")]

    reasons, unknowns = [], []
    for c in (l2[:2] + l3[:1]):
        reasons.append({"text": c["body"][:180], "claim_id": c["claim_id"],
                        "layer": c["layer"]})

    if not claims or score < 0.01:
        verdict = "unknown"
        unknowns.append("no evidence matched this intent")
    elif not l2 and not l3:
        verdict = "unknown"
        unknowns.append("only a description exists: no tests and no usage history")
    elif l2 and not l3:
        verdict = "use_as_is" if not refusals else "extend"
        unknowns.append("nobody outside its own team is known to have used this")
    elif l3 and any(c["kind"] in ("outcome", "pitfall") for c in l3):
        verdict = "extend"
    else:
        verdict = "use_as_is"

    if limits:
        unknowns.append(f"{len(limits)} documented limit(s) -- check them against your scale")

    return Verdict(asset_id=str(claims and claims[0].get("asset_id") or ""),
                   name=name, verdict=verdict, reasons=reasons,
                   unknowns=unknowns, score=score)


def find_existing(user_id: uuid.UUID | str, intent: str,
                  keywords: list[str] | None = None, top: int = 5) -> list[Verdict]:
    """The one function the MCP tool, the CLI and the UI all call."""
    from .db import as_user

    out: list[Verdict] = []
    with as_user(user_id) as conn:
        for row in search(conn, intent, keywords)[:top]:
            claims = load_claims(conn, row["asset_id"])
            v = judge(intent, row["name"], claims, row["score"])
            v.asset_id = str(row["asset_id"])
            out.append(v)
    return out
