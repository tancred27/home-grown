"""MCP server: find_existing and friends for any MCP-capable coding agent.

A thin adapter over the same functions the CLI and the gate-2 harness call (design
section 4: REST/CLI/MCP are routes onto one core, so route choice can be measured).
Tools (design section 7):

  find_existing          read-only   has the org already got this?
  get_asset              read-only   one card; `detail` only with source access
  report_reuse_outcome   write       reused / extended / rejected, and why

Run (stdio):   python -m orgmem.mcp_server
Register with Claude Code:
  claude mcp add orgmem -e ORGMEM_USER=gate2-dev -- <repo>/.venv/Scripts/python -m orgmem.mcp_server

Identity: $ORGMEM_USER names the app_user. The design's real path is an OAuth 2.1
resource server that exchanges the caller's token (section 8); stdio + env var is the
local stand-in, and every query still runs under that user's row-level security.
"""

from __future__ import annotations

import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .cli import log_question, resolve_user
from .db import as_user
from .search import find_existing as _find
from .tools_text import FIND_EXISTING_DESCRIPTION

server = MCPServer(
    name="orgmem",
    instructions=("Organization capability memory. Before writing a new helper, utility, "
                  "service or integration, call find_existing to check whether the "
                  "organization already has one."),
)


def _user() -> str:
    return resolve_user(os.environ.get("ORGMEM_USER", "gate2-dev"))


@server.tool(name="find_existing", description=FIND_EXISTING_DESCRIPTION,
             annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def find_existing(intent: str, keywords: list[str] | None = None,
                  kind: str = "any") -> str:
    """intent: what you need, in one or two sentences, with constraints.
    keywords: synonyms and other names someone might have used for this.
    kind: any | library | service | script | dataset | solved_problem."""
    user = _user()
    verdicts = _find(user, intent, keywords or [])
    log_question(user, intent, keywords or [], "mcp", verdicts)
    return json.dumps({"results": [{
        "asset_id": v.asset_id, "name": v.name.rsplit("/", 1)[-1], "verdict": v.verdict,
        "reasons": [r["text"] for r in v.reasons[:3]], "unknowns": v.unknowns[:3],
    } for v in verdicts], "note": None if verdicts else "nothing found"}, indent=1)


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
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
