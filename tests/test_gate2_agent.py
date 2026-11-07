"""The gate-2 graph: routing, the hook, interrupt/resume, and durability.

These use ScriptedModel and a stub finder, so they test the harness -- not whether any
real model calls the tool. Run: .venv/Scripts/python -m pytest -q tests/test_gate2_agent.py
"""

from __future__ import annotations

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from homegrown.agent import Workspace, build_graph, initial_state, make_tools, score_run
from homegrown.agent.scripted import ScriptedModel

GOLD = "asset-retry-client"
TASK = "Add retries with exponential backoff to our HTTP client."


def stub_finder(hit: bool):
    def finder(intent, keywords):
        if not hit:
            return [{"asset_id": "asset-unrelated", "name": "clock-panel",
                     "verdict": "not_a_fit", "reasons": [], "unknowns": []}]
        return [{"asset_id": GOLD, "name": "retry-client", "verdict": "use_as_is",
                 "reasons": ["Asserted by its own tests: retries with backoff."],
                 "unknowns": []}]
    return finder


def run(behaviour, variant, tmp_path, hit=True, resume="reuse", saver=None):
    ws = Workspace(tmp_path / f"{behaviour}-{variant}")
    finder = stub_finder(hit)
    graph = build_graph(ScriptedModel(behaviour=behaviour), make_tools(ws, variant, finder),
                        finder, checkpointer=saver or InMemorySaver())
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
    out = graph.invoke(initial_state(TASK, variant), cfg)
    interrupts = 0
    while out.get("__interrupt__"):
        interrupts += 1
        out = graph.invoke(Command(resume={"decision": resume, "asset_id": GOLD}), cfg)
    return out, interrupts, ws


def test_eager_model_without_hook_builds_unchecked(tmp_path):
    out, interrupts, ws = run("eager", "description", tmp_path)
    s = score_run(out["events"], GOLD)
    assert s["outcome"] == "built_unchecked"
    assert not s["checked_before_write"] and interrupts == 0
    assert (ws.root / "impl.py").is_file()


def test_careful_model_checks_first_and_is_asked(tmp_path):
    out, interrupts, ws = run("careful", "none", tmp_path)
    s = score_run(out["events"], GOLD)
    assert s["model_called_before_write"] and s["found_gold"]
    assert interrupts == 1 and s["decision"] == "reuse" and s["outcome"] == "reused"


def test_hook_catches_an_eager_model_and_blocks_the_write(tmp_path):
    out, interrupts, ws = run("eager", "hook", tmp_path)
    s = score_run(out["events"], GOLD)
    # The model never chose to check; the graph did.
    assert not s["model_called_before_write"]
    assert s["checked_before_write"] and s["found_gold"]
    assert interrupts == 1 and s["outcome"] == "reused"
    assert not (ws.root / "impl.py").exists(), "the duplicate must not be written"
    assert any(e["type"] == "write_blocked" for e in out["events"])


def test_hook_with_build_anyway_lets_the_write_through(tmp_path):
    out, interrupts, ws = run("eager", "hook", tmp_path, resume="build")
    s = score_run(out["events"], GOLD)
    assert interrupts == 1 and s["decision"] == "build"
    assert s["outcome"] == "built_after_check" and (ws.root / "impl.py").is_file()


def test_no_match_means_no_interrupt(tmp_path):
    out, interrupts, ws = run("eager", "hook", tmp_path, hit=False)
    s = score_run(out["events"], GOLD)
    assert interrupts == 0 and s["checked_before_write"] and not s["found_gold"]
    assert s["outcome"] == "built_after_check"


def test_instruction_variant_changes_only_the_system_prompt():
    from homegrown.agent.graph import system_prompt
    from homegrown.agent.tools import INSTRUCTION_LINE
    assert INSTRUCTION_LINE in system_prompt("instruction")
    assert INSTRUCTION_LINE in system_prompt("all")
    assert INSTRUCTION_LINE not in system_prompt("none")


def test_workspace_refuses_paths_outside_it(tmp_path):
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(ValueError):
        ws.write("../escape.txt", "x")


def test_pause_survives_a_restart_with_the_postgres_checkpointer(tmp_path):
    """The point of the Postgres checkpointer: an interrupted run resumes in a NEW graph
    instance -- standing in for a new process -- from the saved checkpoint."""
    from langgraph.checkpoint.postgres import PostgresSaver

    from homegrown.config import settings

    try:
        cm = PostgresSaver.from_conn_string(settings.admin_dsn)
        saver = cm.__enter__()
    except Exception as e:  # pragma: no cover - only when the DB is down
        pytest.skip(f"postgres unavailable: {e}")
    try:
        saver.setup()
        ws = Workspace(tmp_path / "durable")
        finder = stub_finder(True)
        cfg = {"configurable": {"thread_id": f"test-{uuid.uuid4().hex}"}}

        g1 = build_graph(ScriptedModel(behaviour="eager"), make_tools(ws, "hook", finder),
                         finder, checkpointer=saver)
        out = g1.invoke(initial_state(TASK, "hook"), cfg)
        assert out.get("__interrupt__"), "expected to pause for a reuse decision"
        del g1

        g2 = build_graph(ScriptedModel(behaviour="eager"), make_tools(ws, "hook", finder),
                         finder, checkpointer=saver)
        out = g2.invoke(Command(resume={"decision": "reuse", "asset_id": GOLD}), cfg)
        assert not out.get("__interrupt__")
        assert score_run(out["events"], GOLD)["outcome"] == "reused"
    finally:
        cm.__exit__(None, None, None)
