"""A small coding agent used to test whether coding agents check HomeGrown before
building. See graph.py for the graph and tools.py for the prompt variants
("nudges"); scripts/gate2_run.py runs the experiment.
"""

from .graph import build_graph, initial_state, score_run  # noqa: F401
from .tools import VARIANTS, Workspace, compact_verdicts, make_tools  # noqa: F401
