"""Gate-2 coding agent: does a coding agent check the memory before building?

See graph.py for the graph, tools.py for the nudge conditions, and
scripts/gate2_run.py for the experiment runner.
"""

from .graph import build_graph, initial_state, score_run  # noqa: F401
from .tools import VARIANTS, Workspace, compact_verdicts, make_tools  # noqa: F401
