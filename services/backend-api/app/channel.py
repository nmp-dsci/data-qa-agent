"""Client channel (entry point) detection for query-run auditing.

Every run recorded in ``app.query_runs`` is attributed to the channel it came
through, alongside the user. The web app sends ``X-Client-Channel: web``; a
request without that header is a direct hit on the backend API and is recorded
as ``api``. Future channels (slack, mobile, ...) just send their own value — no
code change here is needed to *store* a new channel, only to originate one.
"""

from __future__ import annotations

from fastapi import Header

# When no client identifies itself, the request reached the backend API directly.
DEFAULT_CHANNEL = "api"
# Guard against a malformed/oversized header polluting the audit column.
_MAX_LEN = 32


def get_channel(x_client_channel: str | None = Header(default=None)) -> str:
    """FastAPI dependency: the normalized client channel for this request.

    Lower-cased and trimmed so 'Web' and 'web ' both store as 'web'; falls back
    to ``api`` when the header is missing, blank, or implausibly long.
    """
    if not x_client_channel:
        return DEFAULT_CHANNEL
    channel = x_client_channel.strip().lower()
    if not channel or len(channel) > _MAX_LEN:
        return DEFAULT_CHANNEL
    return channel
