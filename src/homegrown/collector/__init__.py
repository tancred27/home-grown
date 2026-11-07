"""The collection agent: watches commits and documentation pages, decides what is worth
remembering, and stores it -- with who built it, their team, LOB and manager, and
whether it is in dev or prod. See graph.py."""

from .events import ChangedFile, Event, enqueue  # noqa: F401
from .graph import Deps, build_collector, run_event  # noqa: F401
