"""The MCP server, in-process: tool surface, annotations, and RLS on every call."""

from __future__ import annotations

import asyncio
import json

import pytest

from homegrown import mcp_server
from homegrown.tools_text import FIND_EXISTING_DESCRIPTION


def _text(result) -> str:
    """call_tool returns content blocks (and possibly structured output); take the text."""
    blocks = result[0] if isinstance(result, tuple) else result
    blocks = getattr(blocks, "content", blocks)
    return "".join(getattr(b, "text", "") for b in blocks)


def test_tool_surface_and_annotations():
    tools = {t.name: t for t in asyncio.run(mcp_server.server.list_tools())}
    assert set(tools) == {"find_existing", "ask", "get_asset", "report_reuse_outcome", "explain"}
    assert tools["explain"].annotations.read_only_hint is True
    assert tools["find_existing"].description == FIND_EXISTING_DESCRIPTION
    assert tools["find_existing"].annotations.read_only_hint is True
    assert tools["report_reuse_outcome"].annotations.read_only_hint is False
    assert "intent" in tools["find_existing"].input_schema["properties"]


def test_find_existing_over_mcp_returns_verdicts(monkeypatch):
    monkeypatch.setenv("HOMEGROWN_USER", "gate2-dev")
    try:
        res = asyncio.run(mcp_server.server.call_tool(
            "find_existing", {"intent": "collapse request paths with ids into templates",
                              "keywords": ["cardinality"]}))
    except SystemExit as e:  # pragma: no cover
        pytest.skip(f"no corpus/user: {e}")
    data = json.loads(_text(res))
    assert data["results"], "expected candidates from the corpus"
    assert {"asset_id", "name", "verdict"} <= set(data["results"][0])


def test_get_asset_hides_existence_of_invisible_assets(monkeypatch):
    """An id the caller cannot see answers exactly like an id that does not exist."""
    from homegrown.cli import add_user
    add_user("test-mcp-user", [])
    monkeypatch.setenv("HOMEGROWN_USER", "test-mcp-user")
    res = asyncio.run(mcp_server.server.call_tool(
        "get_asset", {"asset_id": "00000000-0000-0000-0000-000000000000"}))
    assert json.loads(_text(res)) == {"error": "not found"}


def test_find_existing_says_whether_the_caller_can_read_the_code(monkeypatch):
    """Every result carries code access that matches source_acl, so an agent never
    assumes it can build against code it cannot read."""
    from homegrown.cli import resolve_user
    from homegrown.db import admin_conn

    for login in ("gate2-dev", "dev-cloudcost-exporter"):
        try:
            uid = resolve_user(login)
        except SystemExit:  # pragma: no cover
            pytest.skip(f"no user {login}")
        with admin_conn() as conn, conn.cursor() as cur:
            cur.execute("select source_ref from source_acl where user_id = %s", (uid,))
            readable = {r[0] for r in cur.fetchall()}
            cur.execute("select id::text, source_ref from asset")
            refs = dict(cur.fetchall())
        monkeypatch.setenv("HOMEGROWN_USER", login)
        res = asyncio.run(mcp_server.server.call_tool(
            "find_existing", {"intent": "load test Kafka from k6"}))
        for r in json.loads(_text(res))["results"]:
            assert r["can_read_code"] == (refs[r["asset_id"]] in readable), r["name"]
            assert r["how_to_get"].startswith(
                "you can read" if r["can_read_code"] else "the code is private to you")
