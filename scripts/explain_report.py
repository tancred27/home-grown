"""Where do right answers get lost? A report over stored answer traces (operators only).

Give it the questions whose right answer you know, one JSON object per line:
  {"question_id": "...", "expected": "<asset unit_id or asset id>"}
For each one it reads the trace (owner connection) and says where the expected asset was
lost, in pipeline order:

  not indexed       the asset is not in the database (crawl or intake problem)
  not retrieved     not among the candidates the trace kept (outside the fused top 30):
                    neither keyword nor vector search ranked it highly
  ranked too low    retrieved, but below the judge's shortlist
  judged not a fit  the judge saw it and rejected it (the trace has the reason)
  not shown         judged a fit or unknown, but below the shown results
  found             shown to the asker

For losses it prints the evidence from the trace: the expected asset's keyword and vector
ranks, its best-matching fields and the judge's reason. Without a file, it summarises
questions marked answered_well = false.

Usage:
  python -I scripts/explain_report.py [expected.jsonl] [--since-days 30]
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from homegrown.config import settings
from homegrown.db import admin_conn


def _asset_id(conn, ref: str) -> str | None:
    row = conn.execute("select id from asset where id::text = %s or unit_id = %s",
                       (ref, ref)).fetchone()
    return str(row[0]) if row else None


def classify(trace: dict, expected: str | None) -> tuple[str, dict]:
    if expected is None:
        return "not indexed", {}
    c = trace.get("candidates", {}).get(expected)
    if c is None:
        return "not retrieved", {}
    shortlist = set(trace.get("judge", {}).get("shortlist") or [])
    shown = {a["asset_id"] for a in trace.get("shown") or trace.get("answer") or []}
    evidence = {k: c.get(k) for k in ("fused_rank", "keyword_rank", "vector_rank")}
    evidence["best_fields"] = [f"{b['field']} ({b['similarity']})"
                               for b in (c.get("vector") or {}).get("best_fields", [])]
    evidence["top_terms"] = [f"{t['term']} ({t['contribution']})"
                             for t in (c.get("keyword") or {}).get("terms", [])[:3]]
    if c.get("judge"):
        evidence["judge"] = c["judge"]
    if expected in shown:
        return "found", evidence
    if shortlist and expected not in shortlist:
        return "ranked too low", evidence
    if (c.get("judge") or {}).get("verdict") == "not_a_fit":
        return "judged not a fit", evidence
    return "not shown", evidence


def main(argv: list[str]) -> int:
    days = int(argv[argv.index("--since-days") + 1]) if "--since-days" in argv else \
        settings.trace_retention_days
    files = [a for a in argv if a.endswith(".jsonl")]
    with admin_conn() as conn:
        if files:
            rows = [json.loads(line) for line in open(files[0], encoding="utf-8") if line.strip()]
            items = []
            for r in rows:
                t = conn.execute("select trace from query_trace where question_id::text = %s",
                                 (r["question_id"],)).fetchone()
                if t:
                    items.append((r["question_id"], t[0], _asset_id(conn, r["expected"])))
        else:
            items = [(str(q), t, None) for q, t in conn.execute(
                "select q.id, t.trace from question q join query_trace t "
                "on t.question_id = q.id where q.answered_well is false "
                "and t.created_at > now() - make_interval(days => %s)",
                (days,)).fetchall()]
    if not items:
        print("no traces found")
        return 0
    if not files:
        print(f"{len(items)} questions marked answered badly in the last {days} days:")
        for qid, t, _ in items:
            top = sorted(t.get("candidates", {}).items(),
                         key=lambda kv: kv[1].get("fused_rank") or 99)
            print(f"- {qid}  {t.get('question', '')[:80]!r}  top: "
                  + ", ".join(f"{c.get('name')} [{(c.get('judge') or {}).get('verdict')}]"
                              for _, c in top[:3]))
        return 0
    stages = Counter()
    for qid, t, exp in items:
        stage, ev = classify(t, exp)
        stages[stage] += 1
        if stage != "found":
            print(f"- {qid}  {stage}: {json.dumps(ev, default=str)[:300]}")
    print("\nwhere the expected answer was lost:")
    for s in ("found", "not shown", "judged not a fit", "ranked too low", "not retrieved",
              "not indexed"):
        print(f"  {s:18} {stages[s]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
