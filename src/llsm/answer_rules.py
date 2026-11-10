"""How the model should shape an answer for the chat pane. One place, used by every backend.

Read by mcp_server.INSTRUCTIONS_BASE (Claude Code and any MCP client),
claude_code_backend.PERSISTENT_SYSTEM_PROMPT and anthropic_backend.SYSTEM_PROMPT.
The pane renders markdown tables, lists and code (conversation/markdown_lite.py),
so these rules ask for the shapes it draws well. Kept import-free.
"""

ANSWER_RULES = (
    "Answer in at most 5 lines, put a unit on every number, and show results as a table. "
    "Use a markdown table for more than 3 items with 2 or more attributes each, "
    "with at most 4 columns and the unit in the header cell (e.g. 'kh (kN/m³)'), not in every cell. "
    "Answer in the user's language."
)
