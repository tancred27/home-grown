"""The gate-2 coding agent, as a LangGraph graph.

    START -> agent -> (no tool calls) ---------------------------------------> END
               |
               +--> hook_check --(strong match)--> decide --> tools --> agent
               |        |                            ^ interrupt()
               |        +--(no match)--------------> tools
               |
               +--> tools --(model's own find_existing found a strong match)--> decide --> agent
                        +--(otherwise)--> agent

* `agent` calls the model with the tools bound.
* `hook_check` only exists in the "hook" condition: when the model's first write is about
  to run and nothing has been checked yet, the GRAPH calls find_existing on the ticket
  itself. Adherence then stops depending on the model choosing to call the tool.
* `decide` pauses with `interrupt()` whenever a check returns a use-as-is / extend
  candidate, and asks the developer: reuse, or build anyway? With a Postgres
  checkpointer the pause survives a process restart; resume with
  `Command(resume={"decision": "reuse" | "build", "asset_id": ...})`.
* Every check and every write is appended to `events`, which is what gate 2 measures:
  did a check happen before the first write, and was it the model or the hook?
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from .tools import INSTRUCTION_LINE, VARIANTS

STRONG = ("use_as_is", "extend")

BASE_SYSTEM = (
    "You are a coding agent working in a service's repository. Complete the developer's "
    "ticket by writing code into the workspace with write_file. Keep the change small and "
    "focused. When you are done, reply with a short summary of what you did."
)


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    task: str
    variant: str
    events: Annotated[list, operator.add]
    step: int
    checked: bool
    decided: bool
    decision: str | None
    candidates: list            # strong candidates awaiting a decision
    pending_from_hook: bool     # decide was entered from hook_check
    block_next_write: bool      # developer chose reuse: refuse the write that triggered it


def system_prompt(variant: str) -> str:
    if variant in ("instruction", "all"):
        return BASE_SYSTEM + "\n\n" + INSTRUCTION_LINE
    return BASE_SYSTEM


def _strong(results: list[dict]) -> list[dict]:
    return [r for r in results if r.get("verdict") in STRONG]


def build_graph(model, tools: list, finder, checkpointer=None, max_steps: int = 12):
    """`model` is any LangChain chat model supporting bind_tools; `finder` is the same
    callable the find_existing tool wraps, used directly by the hook."""
    by_name = {t.name: t for t in tools}
    bound = model.bind_tools(tools)

    def agent(state: AgentState) -> dict:
        step = state.get("step", 0) + 1
        msgs = [SystemMessage(system_prompt(state["variant"]))] + state["messages"]
        return {"messages": [bound.invoke(msgs)], "step": step}

    def route_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return END
        if state.get("step", 0) >= max_steps:
            return END
        wants_write = any(tc["name"] == "write_file" for tc in last.tool_calls)
        if state["variant"] == "hook" and wants_write and not state.get("checked"):
            return "hook_check"
        return "tools"

    def hook_check(state: AgentState) -> dict:
        results = finder(state["task"], [])
        strong = _strong(results)
        return {
            "checked": True,
            "events": [{"type": "find_existing", "by": "hook", "step": state.get("step", 0),
                        "results": [r["name"] for r in results[:5]],
                        "asset_ids": [r["asset_id"] for r in results[:5]],
                        "strong": [r["asset_id"] for r in strong]}],
            "candidates": strong if not state.get("decided") else [],
            "pending_from_hook": bool(strong) and not state.get("decided"),
        }

    def route_hook(state: AgentState) -> str:
        return "decide" if state.get("pending_from_hook") else "tools"

    def tools_node(state: AgentState) -> dict:
        last = state["messages"][-1]
        out_msgs, events, strong_found = [], [], []
        block = state.get("block_next_write", False)
        checked = state.get("checked", False)
        for tc in last.tool_calls:
            name, args = tc["name"], tc.get("args", {})
            if name == "write_file" and block:
                content = (
                    "NOT WRITTEN. The organization already has an implementation of this "
                    f"({', '.join(c['name'] for c in state.get('candidates', []))}) and the "
                    "developer chose to reuse it. Integrate the existing asset instead of "
                    "reimplementing it.")
                block = False
                events.append({"type": "write_blocked", "path": args.get("path"),
                               "step": state.get("step", 0)})
            elif name in by_name:
                try:
                    content = by_name[name].invoke(args)
                except Exception as e:      # a tool error goes back to the model, not up
                    content = f"error: {type(e).__name__}: {e}"
                if name == "find_existing":
                    checked = True
                    results = json.loads(content).get("results", []) \
                        if content.startswith("{") else []
                    strong = _strong(results)
                    strong_found.extend(strong)
                    events.append({"type": "find_existing", "by": "model",
                                   "step": state.get("step", 0),
                                   "intent": args.get("intent", "")[:200],
                                   "keywords": args.get("keywords", []),
                                   "asset_ids": [r["asset_id"] for r in results[:5]],
                                   "strong": [r["asset_id"] for r in strong]})
                elif name == "write_file":
                    events.append({"type": "write", "path": args.get("path"),
                                   "chars": len(args.get("content", "")),
                                   "step": state.get("step", 0)})
            else:
                content = f"error: unknown tool {name}"
            out_msgs.append(ToolMessage(content=str(content), tool_call_id=tc["id"],
                                        name=name))
        update: dict[str, Any] = {"messages": out_msgs, "events": events,
                                  "checked": checked, "block_next_write": block,
                                  "pending_from_hook": False}
        if strong_found and not state.get("decided"):
            update["candidates"] = strong_found
        return update

    def route_tools(state: AgentState) -> str:
        if state.get("candidates") and not state.get("decided"):
            return "decide"
        return "agent"

    def decide(state: AgentState) -> dict:
        # Everything before interrupt() must be deterministic: on resume LangGraph re-runs
        # this node from the top and interrupt() returns the resume value instead.
        answer = interrupt({
            "question": "This may already exist. Reuse it, or build anyway?",
            "task": state["task"],
            "candidates": state.get("candidates", []),
        })
        decision = (answer or {}).get("decision", "build")
        update: dict[str, Any] = {
            "decided": True, "decision": decision,
            "events": [{"type": "decision", "decision": decision,
                        "asset_id": (answer or {}).get("asset_id"),
                        "via": "hook" if state.get("pending_from_hook") else "model"}],
        }
        if state.get("pending_from_hook"):
            # The model's write call is still pending and must get a ToolMessage next.
            update["block_next_write"] = decision == "reuse"
        else:
            note = ("Developer decision: reuse the existing asset rather than "
                    "reimplementing it." if decision == "reuse" else
                    "Developer decision: build it anyway.")
            update["messages"] = [HumanMessage(note)]
        return update

    def route_decide(state: AgentState) -> str:
        return "tools" if state.get("pending_from_hook") else "agent"

    g = StateGraph(AgentState)
    g.add_node("agent", agent)
    g.add_node("hook_check", hook_check)
    g.add_node("tools", tools_node)
    g.add_node("decide", decide)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route_agent, ["hook_check", "tools", END])
    g.add_conditional_edges("hook_check", route_hook, ["decide", "tools"])
    g.add_conditional_edges("tools", route_tools, ["decide", "agent"])
    g.add_conditional_edges("decide", route_decide, ["tools", "agent"])
    return g.compile(checkpointer=checkpointer)


def initial_state(task: str, variant: str) -> AgentState:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    return {"messages": [HumanMessage(f"Ticket: {task}")], "task": task,
            "variant": variant, "events": [], "step": 0, "checked": False,
            "decided": False, "decision": None, "candidates": [],
            "pending_from_hook": False, "block_next_write": False}


def score_run(events: list[dict], gold_asset_id: str | None) -> dict:
    """What gate 2 measures, from one run's event log."""
    first_write = next((i for i, e in enumerate(events) if e["type"] == "write"), None)
    checks = [(i, e) for i, e in enumerate(events) if e["type"] == "find_existing"]

    def before(i):
        return first_write is None or i < first_write

    model_checks = [(i, e) for i, e in checks if e["by"] == "model"]
    found = bool(gold_asset_id) and any(gold_asset_id in e["asset_ids"] for _, e in checks)
    decision = next((e["decision"] for e in events if e["type"] == "decision"), None)
    wrote = sum(1 for e in events if e["type"] == "write")
    if decision == "reuse":
        outcome = "reused"
    elif first_write is not None and not any(before(i) for i, _ in checks):
        outcome = "built_unchecked"
    elif wrote:
        outcome = "built_after_check"
    else:
        outcome = "no_write"
    return {
        "model_called_before_write": any(before(i) for i, _ in model_checks),
        "checked_before_write": any(before(i) for i, _ in checks),
        "found_gold": found,
        "decision": decision,
        "writes": wrote,
        "outcome": outcome,
    }
