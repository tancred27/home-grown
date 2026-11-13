"""MCP server: find_existing and friends for any MCP-capable coding agent.

Tools:
  find_existing          read-only   has the org already got this?
  get_asset              read-only   one card; `detail` only with source access
  report_reuse_outcome   write       reused / extended / rejected, and why

Run (stdio):   python -m homegrown.mcp_server
Register with Claude Code:
  claude mcp add homegrown -e HOMEGROWN_USER=alice -- \
      <repo>/.venv/Scripts/python -m homegrown.mcp_server

Identity: $HOMEGROWN_USER names the app_user, and every query runs under that user's
row-level security. A real deployment would take identity from an OAuth token.
"""

from __future__ import annotations

import json
import os
import time

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import telemetry
from .cli import log_question, resolve_user
from .db import as_user
from .models import display_name
from .search import CLARIFY_HOW
from .search import find_existing as _find
from .tools_text import FIND_EXISTING_DESCRIPTION

server = MCPServer(
    name="homegrown",
    instructions=("Organization capability memory. Before writing a new helper, utility, "
                  "service or integration, call find_existing to check whether the "
                  "organization already has one."),
)


def _user() -> str:
    return resolve_user(os.environ.get("HOMEGROWN_USER", ""))


@server.tool(name="find_existing", description=FIND_EXISTING_DESCRIPTION,
             annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def find_existing(intent: str, keywords: list[str] | None = None,
                  kind: str = "any") -> str:
    """intent: what you need, in one or two sentences, with constraints.
    keywords: synonyms and other names someone might have used for this.
    kind: any | library | service | script | dataset | solved_problem."""
    from . import explain
    from .config import settings
    user = _user()
    t0 = time.monotonic()
    trace = explain.new(intent, keywords) if settings.trace_enabled else None
    clarify: dict = {}
    verdicts = _find(user, intent, keywords or [], trace=trace, clarify=clarify)
    telemetry.record_question("mcp", "find", time.monotonic() - t0)
    qid = log_question(user, intent, keywords or [], "mcp", verdicts, trace)
    access = _access(user, [v.asset_id for v in verdicts])
    return json.dumps({"results": [{
        "asset_id": v.asset_id, "name": display_name(v.name), "verdict": v.verdict,
        "reasons": [r["text"] for r in v.reasons[:3]], "unknowns": v.unknowns[:3],
        **access.get(v.asset_id, {}),
    } for v in verdicts if v.asset_id in access],
        "question_id": qid,
        "clarify": {**clarify, "how_to_answer": CLARIFY_HOW} if clarify else None,
        "note": None if verdicts else "nothing found"}, indent=1)


@server.tool(name="explain", description=(
    "Why did a find_existing answer come out that way? Give the question_id it returned. "
    "Shows each step: keyword and vector matches per candidate, how they were combined, the "
    "judge's verdicts and reasons, and where each candidate's description came from."),
             annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def explain_answer(question_id: str) -> str:
    from .explain import load
    trace = load(_user(), question_id)
    return json.dumps(trace if trace is not None else
                      {"note": "no trace for that question id"}, indent=1, default=str)


def _access(user: str, asset_ids: list[str]) -> dict[str, dict]:
    """For each asset the caller may discover: can they read its code, who owns it, and
    how to get it. Runs as the caller, so an asset they cannot discover has no row and is
    dropped from the results rather than described."""
    if not asset_ids:
        return {}
    with as_user(user) as conn, conn.cursor() as cur:
        cur.execute("""
            select a.id::text, caller_can_read_source(a.source_ref), a.source_ref,
                   t.name, lead.display_name
              from asset a
              left join team t on t.id = a.owner_team
              left join person lead on lead.team_id = t.id and lead.handle like 'lead-%%'
             where a.id = any(%s::uuid[])""", (asset_ids,))
        rows = cur.fetchall()
    out = {}
    for aid, can_read, source_ref, team, lead in rows:
        contact = lead or team or "its owner"     # a lead's name already names the team
        out[aid] = {
            "owner_team": team,
            "can_read_code": bool(can_read),
            "how_to_get": (f"you can read the code: {source_ref}" if can_read else
                           f"the code is private to you: ask {contact} for access to "
                           f"{source_ref}"),
        }
    return out


@server.tool(name="ask",
             description=("Has someone in the organization already done this or solved "
                          "this problem? Answers yes / no / unknown / possibly, and for "
                          "each match: who built it, their team, line of business and "
                          "manager, whether it is in dev or prod, and how to get access."),
             annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def ask_tool(question: str, keywords: list[str] | None = None) -> str:
    from .answer import ask
    out = ask(_user(), question, keywords or [], route="mcp")
    return json.dumps({k: out[k] for k in ("answer", "verdict_source", "matches", "clarify")},
                      indent=1, default=str)


@server.tool(name="get_asset", description="Fetch one asset's card by id.",
             annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def get_asset(asset_id: str) -> str:
    with as_user(_user()) as conn, conn.cursor() as cur:
        cur.execute("select fields, detail, has_source_access from card_visible "
                    "where asset_id = %s", (asset_id,))
        row = cur.fetchone()
    if not row:
        # Same answer for "does not exist" and "you may not see it": no existence leak.
        return json.dumps({"error": "not found"})
    fields, detail, access = row
    return json.dumps({"card": fields, "detail": detail if access else None,
                       "has_source_access": access}, indent=1)


@server.tool(name="report_reuse_outcome",
             description="Record that you reused, extended or rejected an asset, and why.",
             annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                         idempotent_hint=False, open_world_hint=False))
def report_reuse_outcome(asset_id: str, intent: str, outcome: str,
                         reason: str = "") -> str:
    if outcome not in ("reused", "extended", "rejected", "abandoned"):
        return json.dumps({"error": "outcome must be reused|extended|rejected|abandoned"})
    user = _user()
    with as_user(user) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into reuse_outcome (asset_id, intent, outcome, reason, reported_by, "
            "reporter_kind) values (%s,%s,%s,%s,%s,'agent') returning id",
            (asset_id, intent, outcome, reason, user))
        rid = cur.fetchone()[0]
        conn.commit()
    return json.dumps({"recorded": str(rid), "reporter_kind": "agent"})


def main() -> None:
    telemetry.init("mcp")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
