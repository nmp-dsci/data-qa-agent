"""Non-UI front doors (s35): webhook, Slack, and the MCP server's HTTP client.

Each surface is a thin adapter. Everything they have in common — the daily cap,
RLS scoping, degraded-mode fallback, the audit write — lives in
``routers.ask.run_question`` and is shared, not reimplemented, so four front
doors don't become four security surfaces.
"""
