"""The MCP server, in-process: tool surface, annotations, and RLS on every call."""

from __future__ import annotations

import asyncio
import json

import pytest

from orgmem import mcp_server
from orgmem.tools_text import FIND_EXISTING_DESCRIPTION


def _text(result) -> str:
    """call_tool returns content blocks (and possibly structured output); take the text."""
    blocks = result[0] if isinstance(result, tuple) else result
    blocks = getattr(blocks, "content", blocks)
    return "".join(getattr(b, "text", "") for b in blocks)


def test_tool_surface_and_annotations():
    tools = {t.name: t for t in asyncio.run(mcp_server.server.list_tools())}
    assert set(tools) == {"find_existing", "get_asset", "report_reuse_outcome"}
    assert tools["find_existing"].description == FIND_EXISTING_DESCRIPTION
    assert tools["find_existing"].annotations.read_only_hint is True
    assert tools["report_reuse_outcome"].annotations.read_only_hint is False
    assert "intent" in tools["find_existing"].input_schema["properties"]


def test_find_existing_over_mcp_returns_verdicts(monkeypatch):
    monkeypatch.setenv("ORGMEM_USER", "gate2-dev")
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
    monkeypatch.setenv("ORGMEM_USER", "gate2-dev")
    res = asyncio.run(mcp_server.server.call_tool(
        "get_asset", {"asset_id": "00000000-0000-0000-0000-000000000000"}))
    assert json.loads(_text(res)) == {"error": "not found"}
