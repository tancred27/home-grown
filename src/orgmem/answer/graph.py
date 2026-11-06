"""The answering agent: "has someone here already done X?"

    retrieve -> judge -> enrich -> compose

Answers yes / no / unknown / possibly, and for every match: who built it, their team,
line of business and manager, whether it is in dev or prod (and how sure we are), and
whether the asker can read the code or must request access.

The answer word is deliberately conservative:
  model verdicts (settings.verdict_mode = "model")
      yes      a candidate was judged use-as-is or extend
      no       every candidate was judged not-a-fit
      unknown  otherwise, including when the model call failed (it fails safe)
  rule verdicts (the default until the API has credit)
      possibly a candidate looks relevant, but the rule-based verdict is unverified --
               on held-out tickets it raised a false alarm on 8 of 8 controls
               (experiments/gate2/RESULTS.md), so it never says a plain "yes"
      unknown  nothing looked relevant; the rules cannot support a confident "no"

Every lookup runs as the asking user under row-level security: a match the asker may
not discover is never returned, and neither is anything about who built it.
"""

from __future__ import annotations

import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import settings
from ..db import as_user
from ..models import display_name
from ..search import find_existing

STRONG = ("use_as_is", "extend")


class AnswerState(TypedDict, total=False):
    user_id: str
    question: str
    keywords: list
    candidates: list
    matches: list
    answer: str
    verdict_source: str
    text: str


def _retrieve_and_judge(state: AnswerState) -> dict:
    vs = find_existing(state["user_id"], state["question"], state.get("keywords") or [])
    cands = [{"asset_id": v.asset_id, "name": display_name(v.name),
              "verdict": v.verdict, "reasons": [r["text"] for r in v.reasons[:3]],
              "unknowns": v.unknowns[:2]} for v in vs]
    return {"candidates": cands, "verdict_source": settings.verdict_mode}


def _unit_path(unit_id: str | None) -> str:
    """'github:repo:helm/chart' -> 'helm/chart'; the repo root ('.') -> ''.

    Two units in one repo often share a display name (a service and its Helm chart);
    without the path an answer lists the same name twice."""
    parts = (unit_id or "").split(":", 2)
    path = parts[2] if len(parts) == 3 else ""
    return "" if path in ("", ".") else path


def _enrich(state: AnswerState) -> dict:
    strong = [c for c in state["candidates"] if c["verdict"] in STRONG][:3]
    out = []
    with as_user(state["user_id"]) as conn, conn.cursor() as cur:
        for c in strong:
            cur.execute("""
                select a.stage, a.stage_confidence, a.stage_confirmed, e.excerpt,
                       t.name, l.name, lead.display_name, a.status,
                       caller_can_read_source(a.source_ref), a.source_ref, a.unit_id
                  from asset a
                  left join team t on t.id = a.owner_team
                  left join lob l on l.id = t.lob_id
                  left join person lead on lead.team_id = t.id
                                       and lead.handle like 'lead-%%'
                  left join evidence e on e.id = a.stage_evidence_id
                 where a.id = %s""", (c["asset_id"],))
            row = cur.fetchone()
            if not row:
                continue          # not discoverable for this user: say nothing about it
            (stage, conf, confirmed, basis, team, lob, lead, status, can_read,
             source_ref, unit_id) = row
            c = {**c, "where": _unit_path(unit_id)}
            cur.execute("""
                select p.display_name, p.handle, pt.name, m.display_name,
                       ac.commits, ac.last_at, ac.role
                  from asset_contributor ac
                  join person p on p.id = ac.person_id
                  left join team pt on pt.id = p.team_id
                  left join person m on m.id = p.manager_id
                 where ac.asset_id = %s
                 order by ac.commits desc nulls last limit 3""", (c["asset_id"],))
            people = [{"name": n, "handle": h, "team": pt, "manager": m, "commits": k,
                       "last_active": la.isoformat() if la else None, "role": r}
                      for n, h, pt, m, k, la, r in cur.fetchall()]
            # Who depends on it. The edge policy only returns an edge when BOTH ends are
            # visible to the asker, so a hidden dependent is never named or counted.
            cur.execute("""
                select distinct s.name, st.name
                  from edge e join asset s on s.id = e.src
                  left join team st on st.id = s.owner_team
                 where e.dst = %s and e.type = 'depends_on' and e.valid_to is null
                   and s.owner_team is distinct from (select owner_team from asset
                                                       where id = %s)""",
                        (c["asset_id"], c["asset_id"]))
            used_by = [{"asset": display_name(n), "team": t} for n, t in cur.fetchall()]
            out.append({**c,
                        "stage": {"stage": stage, "confidence": conf,
                                  "confirmed_by_owner": confirmed, "basis": basis},
                        "owner_team": team, "lob": lob, "team_manager": lead,
                        "built_by": people, "used_by": used_by,
                        "access": ("you can read the code" if can_read else
                                   f"discoverable only: request access to {source_ref} "
                                   f"from {lead or team or 'its owner'}")})
    return {"matches": out}


def _compose(state: AnswerState) -> dict:
    cands, matches = state["candidates"], state["matches"]
    model = state.get("verdict_source") == "model"
    if matches:
        answer = "yes" if model else "possibly"
    elif model and cands and all(c["verdict"] == "not_a_fit" for c in cands):
        answer = "no"
    else:
        answer = "unknown"

    lines = [{"yes": "Yes - this has been done here.",
              "possibly": "Possibly - these look relevant, but the verdict is "
                          "rule-based and unverified; check before reusing.",
              "no": "No - nothing in the organisation's memory fits this.",
              "unknown": "Unknown - nothing clearly fits, but that is not proof it "
                         "has not been done."}[answer]]
    for m in matches:
        st = m["stage"]
        stage = f"{st['stage']}" + ("" if st["confirmed_by_owner"] else
                                    f" (inferred, {int((st['confidence'] or 0) * 100)}% "
                                    "confidence, not yet confirmed by the owner)")
        where = f" ({m['where']})" if m.get("where") else ""
        lines.append(f"\n* {m['name']}{where}  [{m['verdict']}]  stage: {stage}")
        lines.append(f"  team: {m['owner_team']}  |  LOB: {m['lob']}  |  "
                     f"manager: {m['team_manager']}")
        for p in m["built_by"]:
            lines.append(f"  built by {p['name']} ({p['team']}, reports to {p['manager']},"
                         f" {p['commits']} commits, last active {(p['last_active'] or '')[:10]})")
        if m.get("used_by"):
            lines.append("  used by: " + ", ".join(f"{u['asset']} ({u['team']})"
                                                   for u in m["used_by"][:5]))
        for r in m["reasons"][:2]:
            lines.append(f"  - {r[:120]}")
        lines.append(f"  access: {m['access']}")
        if st.get("basis"):
            lines.append(f"  stage basis: {st['basis'][:140]}")
    return {"answer": answer, "text": "\n".join(lines)}


def build_answerer(checkpointer=None):
    g = StateGraph(AnswerState)
    g.add_node("retrieve_and_judge", _retrieve_and_judge)
    g.add_node("enrich", _enrich)
    g.add_node("compose", _compose)
    g.add_edge(START, "retrieve_and_judge")
    g.add_edge("retrieve_and_judge", "enrich")
    g.add_edge("enrich", "compose")
    g.add_edge("compose", END)
    return g.compile(checkpointer=checkpointer)


def ask(user_id: str | uuid.UUID, question: str, keywords: list[str] | None = None) -> dict:
    out = build_answerer().invoke({"user_id": str(user_id), "question": question,
                                   "keywords": keywords or []})
    return {"question": question, "answer": out["answer"],
            "verdict_source": out["verdict_source"], "matches": out["matches"],
            "text": out["text"]}
