"""Tool descriptions shared by every route (MCP, the test agent), so all of them use
the same wording. They describe scope, not quality: superlatives in a tool description
pull models toward the wrong tool.
"""

FIND_EXISTING_DESCRIPTION = (
    "Check whether the organization already has a tool, library, service or solved "
    "feature for what you are about to build. Call this BEFORE writing any new helper, "
    "utility, service or integration. Returns up to 5 candidates with a reuse verdict, "
    "evidence, and how to get access. If a match fits but can_read_code is false, do not "
    "build against it and do not quietly write a duplicate: tell the user who owns it "
    "(how_to_get), offer to draft an access request, and let them decide. If the result "
    "has a clarify question, ask the user it and call again with their answer added to "
    "the intent."
)
