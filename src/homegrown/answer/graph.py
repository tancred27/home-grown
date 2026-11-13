"""The answering agent: "has someone here already done X?"

    retrieve -> judge -> enrich -> compose

Lists up to settings.answer_results (5) results: use-as-is first, then related
(extend), then candidates the judge could not rule out; not-a-fit candidates are never
listed. With the requirements judge (settings.model_judge) the list is 3 long
(answer_show), longer only when the next results tie with the third, and each result's
"why" says which of the request's requirements its card shows.

For each: who built it, their team, line of business and manager, whether it is
in dev or prod (and how sure we are), and whether the asker can read the code or must
request access.

The answer word is deliberately conservative:
  model verdicts (settings.verdict_mode = "model")
      yes      a candidate was judged use-as-is
      related  nothing fits as-is, but a candidate could be extended to fit
      no       every candidate was judged not-a-fit
      unknown  otherwise, including when the model call failed (it fails safe)
  rule verdicts (settings.verdict_mode = "rules")
      possibly a candidate looks relevant; the rules don't read the request, so they
               never say a plain "yes"
      unknown  nothing looked relevant; the rules can't support a confident "no"

Every lookup runs as the asking user under row-level security: a match the asker may
not discover is never returned, and neither is anything about who built it.
"""

from __future__ import annotations

import functools
import time
import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .. import telemetry
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
    trace: dict                     # explain.new(): filled as the answer is built
    clarify: dict                   # the judge's clarifying question, or empty


def _retrieve_and_judge(state: AnswerState) -> dict:
    model = settings.verdict_mode == "model"
    clarify: dict = {}
    vs = find_existing(state["user_id"], state["question"], state.get("keywords") or [],
                       top=settings.answer_results, trace=state.get("trace"), clarify=clarify)
    # In model mode the judge's own reason is unknowns[0] (see search.find_existing).
    cands = [{"asset_id": v.asset_id, "name": display_name(v.name),
              "verdict": v.verdict, "reasons": [r["text"] for r in v.reasons[:3]],
              "why": (v.unknowns[0] if model and v.unknowns else ""),
              "unknowns": v.unknowns[:2]} for v in vs]
    return {"candidates": cands, "verdict_source": settings.verdict_mode,
            "trace": state.get("trace"), "clarify": clarify}


def _unit_path(unit_id: str | None) -> str:
    """'github:repo:helm/chart' -> 'helm/chart'; the repo root ('.') -> ''.

    Two units in one repo often share a display name (a service and its Helm chart);
    without the path an answer lists the same name twice."""
    parts = (unit_id or "").split(":", 2)
    path = parts[2] if len(parts) == 3 else ""
    path = path.split("#", 1)[0]          # crawler units: '<repo path>#<unit slug>'
    return "" if path in ("", ".") else path


def select_results(cands: list[dict], n: int) -> list[dict]:
    """Up to n results: use-as-is, then extend, then unknown, each in search order.
    A candidate judged not-a-fit is never shown."""
    order = ("use_as_is", "extend", "unknown")
    return [c for v in order for c in cands if c["verdict"] == v][:n]


_GROUPS = {"use_as_is": "Use as-is", "extend": "Related, check these",
           "unknown": "Not ruled out"}


def _enrich(state: AnswerState) -> dict:
    shown = select_results(state["candidates"], settings.answer_results)
    out = []
    with as_user(state["user_id"]) as conn, conn.cursor() as cur:
        for c in shown:
            cur.execute("""
                select a.stage, a.stage_confidence, a.stage_confirmed, e.excerpt,
                       t.name, l.name, lead.display_name, a.status,
                       caller_can_read_source(a.source_ref), a.source_ref, a.unit_id,
                       a.parent_id, par.name
                  from asset a
                  left join asset par on par.id = a.parent_id
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
             source_ref, unit_id, parent_id, parent_name) = row
            c = {**c, "where": _unit_path(unit_id),
                 "repo": display_name(parent_name) if parent_name else None}
            # A crawler unit has no history of its own: who built it and its stage come
            # from its repo.
            people_of = parent_id or c["asset_id"]
            if parent_id and stage is None:
                cur.execute("select a.stage, a.stage_confidence, a.stage_confirmed, e.excerpt "
                            "from asset a left join evidence e on e.id = a.stage_evidence_id "
                            "where a.id = %s", (parent_id,))
                prow = cur.fetchone()
                if prow:
                    stage, conf, confirmed, basis = prow
            cur.execute("""
                select p.display_name, p.handle, pt.name, m.display_name,
                       ac.commits, ac.last_at, ac.role
                  from asset_contributor ac
                  join person p on p.id = ac.person_id
                  left join team pt on pt.id = p.team_id
                  left join person m on m.id = p.manager_id
                 where ac.asset_id = %s
                 order by ac.commits desc nulls last limit 3""", (people_of,))
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
    # Units of the same repo stay together, in the order of the repo's best unit.
    first = {}
    for i, m in enumerate(out):
        first.setdefault(m.get("repo") or m["asset_id"], i)
    out.sort(key=lambda m: first[m.get("repo") or m["asset_id"]])
    return {"matches": out}


def _compose(state: AnswerState) -> dict:
    cands, matches = state["candidates"], state["matches"]
    model = state.get("verdict_source") == "model"
    verdicts = {m["verdict"] for m in matches}
    if not model:
        answer = "possibly" if verdicts & set(STRONG) else "unknown"
    elif "use_as_is" in verdicts:
        answer = "yes"
    elif "extend" in verdicts:
        answer = "related"
    elif cands and all(c["verdict"] == "not_a_fit" for c in cands):
        answer = "no"
    else:
        answer = "unknown"

    lines = [{"yes": "Yes - this has been done here.",
              "related": "Related - nothing does exactly this, but these come close; "
                         "check them before building.",
              "possibly": "Possibly - these look relevant, but the verdict is "
                          "rule-based and unverified; check before reusing.",
              "no": "No - nothing in the organisation's memory fits this.",
              "unknown": "Unknown - nothing clearly fits, but that is not proof it "
                         "has not been done."}[answer]]
    if matches and not model:
        lines.append(f"\nTop {len(matches)} result{'s' if len(matches) != 1 else ''}:")
    group = None
    for m in matches:
        if model and m["verdict"] != group:
            group = m["verdict"]
            lines.append(f"\n{_GROUPS.get(group, group)}:")
        st = m["stage"]
        stage = f"{st['stage']}" + ("" if st["confirmed_by_owner"] else
                                    f" (inferred, {int((st['confidence'] or 0) * 100)}% "
                                    "confidence, unconfirmed)")
        where = (f" (in {m['repo']})" if m.get("repo") else
                 f" ({m['where']})" if m.get("where") else "")
        lines.append(f"\n* {m['name']}{where}  [{m['verdict']}]  stage: {stage}")
        lines.append(f"  team: {m['owner_team']}  |  LOB: {m['lob']}  |  "
                     f"manager: {m['team_manager']}")
        for p in m["built_by"]:
            lines.append(f"  built by {p['name']} ({p['team']}, reports to {p['manager']},"
                         f" {p['commits']} commits, last active {(p['last_active'] or '')[:10]})")
        if m.get("used_by"):
            lines.append("  used by: " + ", ".join(f"{u['asset']} ({u['team']})"
                                                   for u in m["used_by"][:5]))
        if m.get("why"):
            lines.append(f"  why: {m['why'][:400]}")
        for r in list(dict.fromkeys(m["reasons"]))[:2]:      # dedupe, keep order
            lines.append(f"  - {r[:120]}")
        lines.append(f"  access: {m['access']}")
        if st.get("basis"):
            lines.append(f"  stage basis: {st['basis'][:140]}")
    cq = state.get("clarify")
    if cq:
        lines.append(f"\nTo narrow this down: {cq['question']} "
                     f"({' / '.join(cq['choices'])}). Ask again with your answer added.")
    return {"answer": answer, "text": "\n".join(lines)}


def _traced(name: str, fn):
    """Run a node in a span that records how many items each list it returns holds.
    Sizes only: the lists hold names and evidence."""
    @functools.wraps(fn)
    def node(state: AnswerState) -> dict:
        with telemetry.span(f"answer.{name}") as sp:
            out = fn(state)
            sp.set_attributes({f"homegrown.{k}": len(v) for k, v in out.items()
                               if isinstance(v, list)})
            return out
    return node


def build_answerer(checkpointer=None):
    g = StateGraph(AnswerState)
    g.add_node("retrieve_and_judge", _traced("retrieve_and_judge", _retrieve_and_judge))
    g.add_node("enrich", _traced("enrich", _enrich))
    g.add_node("compose", _traced("compose", _compose))
    g.add_edge(START, "retrieve_and_judge")
    g.add_edge("retrieve_and_judge", "enrich")
    g.add_edge("enrich", "compose")
    g.add_edge("compose", END)
    return g.compile(checkpointer=checkpointer)


class _V:  # the two fields explain.save reads from a verdict
    def __init__(self, asset_id, verdict):
        self.asset_id, self.verdict = asset_id, verdict


def ask(user_id: str | uuid.UUID, question: str, keywords: list[str] | None = None,
        route: str = "python") -> dict:
    """route (cli, mcp, http) labels the telemetry and the stored question.

    The answer carries a question_id: `homegrown explain <id>` shows why it came out so."""
    from .. import explain
    t0 = time.monotonic()
    trace = explain.new(question, keywords) if settings.trace_enabled else None
    with telemetry.span("answer.ask", {"homegrown.route": route}) as sp:
        out = build_answerer().invoke({"user_id": str(user_id), "question": question,
                                       "keywords": keywords or [], "trace": trace})
        sp.set_attributes({"homegrown.answer": out["answer"],
                           "homegrown.matches": len(out["matches"])})
    telemetry.record_question(route, "ask", time.monotonic() - t0, out["answer"])
    trace = out.get("trace") or trace
    if trace is not None:
        trace["shown"] = [{"asset_id": m.get("asset_id"), "verdict": m.get("verdict")}
                          for m in out["matches"]]
        trace["answer_label"] = out["answer"]
    qid = explain.save(str(user_id), question, keywords or [], route,
                       [_V(m.get("asset_id"), m.get("verdict")) for m in out["matches"]], trace)
    return {"question_id": qid, "question": question, "answer": out["answer"],
            "verdict_source": out["verdict_source"], "matches": out["matches"],
            "clarify": out.get("clarify") or None, "text": out["text"]}
