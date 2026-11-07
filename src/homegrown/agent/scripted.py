"""A scripted chat model, for exercising the graph without an LLM.

PLUMBING ONLY. It proves the graph routes, interrupts, resumes and logs correctly. It says
nothing about whether a real model calls find_existing -- that is gate 2's question, and
it needs a real model (scripts/gate2_run.py --model claude).

Two behaviours, the two ends of what gate 2 is distinguishing:
  eager    writes the implementation immediately, never checks
  careful  calls find_existing with the ticket text first, then writes
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


def _call(name: str, **args) -> dict:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:10]}",
            "type": "tool_call"}


class ScriptedModel(BaseChatModel):
    behaviour: str = Field(default="eager")

    @property
    def _llm_type(self) -> str:
        return f"scripted-{self.behaviour}"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None,
                  **kwargs: Any) -> ChatResult:
        ticket = next((m.content for m in messages if isinstance(m, HumanMessage)), "")
        ticket = str(ticket).removeprefix("Ticket: ")
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        called = {m.name for m in tool_msgs}
        writes = [m for m in tool_msgs if m.name == "write_file"]
        blocked = any(str(m.content).startswith("NOT WRITTEN") for m in writes)
        written = any(str(m.content).startswith("wrote") for m in writes)

        if self.behaviour == "careful" and "find_existing" not in called:
            msg = AIMessage("", tool_calls=[_call("find_existing", intent=ticket,
                                                  keywords=[])])
        elif not writes:
            msg = AIMessage("", tool_calls=[_call(
                "write_file", path="impl.py",
                content=f'"""{ticket}"""\n\ndef todo():\n    raise NotImplementedError\n')])
        elif blocked and not written:
            # Told to reuse: write a short integration note instead of an implementation.
            msg = AIMessage("", tool_calls=[_call(
                "write_file", path="integration.md",
                content="Reusing the existing organisation asset instead of a new one.\n")])
        else:
            msg = AIMessage("Done.")
        return ChatResult(generations=[ChatGeneration(message=msg)])
