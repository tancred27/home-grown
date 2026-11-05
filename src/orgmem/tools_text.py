"""Tool-facing text shared by every route (MCP, the gate-2 harness), so a nudge measured
in one place is the same nudge everywhere. Describes scope, not quality: superlatives in
tool descriptions pull model selection toward the wrong tool (design section 7)."""

FIND_EXISTING_DESCRIPTION = (
    "Check whether the organization already has a tool, library, service or solved "
    "feature for what you are about to build. Call this BEFORE writing any new helper, "
    "utility, service or integration. Returns up to 5 candidates with a reuse verdict, "
    "evidence, and how to get access."
)
