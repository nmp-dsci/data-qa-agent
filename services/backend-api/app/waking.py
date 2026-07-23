"""Classify connect-phase database failures as "waking" (s29).

Aurora Serverless v2 at min 0 ACU auto-pauses after an idle hour; while it
resumes, connects are refused or time out (s29: the login-page requests that
hit this window hung ~30s and looked like a dead app). Those failures are
*retryable* — the API surfaces them as 503 db_warming rather than a 500, and
the frontend login flow waits them out.

Stdlib-only on purpose: the repo-root test suite imports this module without
the backend's fastapi/sqlalchemy stack installed.
"""

from __future__ import annotations

import asyncio
import socket


def is_db_waking(exc: BaseException) -> bool:
    """True when the exception chain says Postgres isn't accepting connections yet.

    Walks the __cause__/__context__ chain because SQLAlchemy wraps the driver
    error (DBAPIError.orig rides along as __cause__). Classified as waking:
      - ConnectionError (refused/reset/aborted) and connect TimeoutError
      - SQLSTATE class 08 (connection exception) or exactly 57P03 "the database
        system is starting up" from asyncpg — not the rest of class 57, which
        includes 57014 query_canceled (statement_timeout killing a runaway
        query: a real bug that must keep its 500 and traceback)
    Chain nodes that prove the failure isn't the database end the walk as
    unclassified — a genuine 500, not a warm-up:
      - DNS failures (gaierror): misconfiguration, retrying can't fix it
      - httpx/httpcore errors: an outbound HTTP call failed (Google's OpenID
        config, the data-agent); the ConnectionError underneath belongs to
        that call, not to Postgres
    """
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, socket.gaierror):
            return False
        if type(e).__module__.partition(".")[0] in ("httpx", "httpcore"):
            return False
        if isinstance(e, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
            return True
        sqlstate = getattr(e, "sqlstate", None)
        if isinstance(sqlstate, str) and (sqlstate[:2] == "08" or sqlstate == "57P03"):
            return True
        e = e.__cause__ or e.__context__
    return False
