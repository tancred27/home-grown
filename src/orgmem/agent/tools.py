"""Tools for the gate-2 coding agent: a sandboxed workspace plus find_existing.

Gate 2 asks whether a coding agent calls find_existing before building something the
organization already has. The tools are deliberately ordinary -- read, write, list, plus
the memory lookup -- so the only thing that varies between conditions is the nudge.

Nudge conditions (docs/design.md section 7, "Getting agents to call it"):
  none         tool present, neutral description
  description  "call this BEFORE writing" tool description (the design's wording)
  instruction  a line in the system prompt, as an AGENTS.md / CLAUDE.md would add
  all          description + instruction together: the strongest prompt-side nudge
  hook         the GRAPH runs find_existing before the first write, whatever the model
               does. The design's fallback if prompts do not move behaviour -- and the one
               study it cites found prompts did not; removing the alternative route did.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import StructuredTool

VARIANTS = ("none", "description", "instruction", "all", "hook")

DESC_NEUTRAL = "Search the organization's capability memory for existing assets."
DESC_NUDGE = (
    "Check whether the organization already has a tool, library, service or solved "
    "feature for what you are about to build. Call this BEFORE writing any new helper, "
    "utility, service or integration. Returns up to 5 candidates with a reuse verdict, "
    "evidence, and how to get access."
)
INSTRUCTION_LINE = (
    "Before writing any new helper, utility, service or integration, call find_existing "
    "to check whether the organization already has one, and reuse or extend it if it does."
)


class Workspace:
    """A directory the agent may touch, and nothing outside it."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"path escapes the workspace: {rel}")
        return p

    def read(self, path: str) -> str:
        p = self._path(path)
        return p.read_text(encoding="utf-8") if p.is_file() else f"(no such file: {path})"

    def write(self, path: str, content: str) -> str:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}"

    def listing(self) -> str:
        files = sorted(str(p.relative_to(self.root)).replace("\\", "/")
                       for p in self.root.rglob("*") if p.is_file())
        return "\n".join(files) or "(empty)"


def compact_verdicts(verdicts) -> list[dict]:
    """What the agent sees from find_existing: short, decision-shaped."""
    out = []
    for v in verdicts:
        out.append({
            "asset_id": v.asset_id,
            "name": v.name.rsplit("/", 1)[-1],
            "verdict": v.verdict,
            "reasons": [r["text"][:160] for r in v.reasons[:3]],
            "unknowns": v.unknowns[:2],
        })
    return out


def make_tools(workspace: Workspace, variant: str, finder) -> list[StructuredTool]:
    """`finder(intent, keywords) -> list[dict]` is injected so tests can stub retrieval."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; expected one of {VARIANTS}")
    desc = DESC_NUDGE if variant in ("description", "all") else DESC_NEUTRAL

    def find_existing(intent: str, keywords: list[str] | None = None) -> str:
        return json.dumps({"results": finder(intent, keywords or [])}, ensure_ascii=False)

    def read_file(path: str) -> str:
        return workspace.read(path)

    def write_file(path: str, content: str) -> str:
        return workspace.write(path, content)

    def list_files() -> str:
        return workspace.listing()

    return [
        StructuredTool.from_function(find_existing, name="find_existing", description=desc),
        StructuredTool.from_function(read_file, name="read_file",
                                     description="Read a file from the project workspace."),
        StructuredTool.from_function(write_file, name="write_file",
                                     description="Create or overwrite a file in the project "
                                                 "workspace."),
        StructuredTool.from_function(list_files, name="list_files",
                                     description="List the files in the project workspace."),
    ]
