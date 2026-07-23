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
      - SQLSTATE class 08 (connection exception) or 57 (operator intervention —
        57P03 "the database system is starting up") from asyncpg
    DNS failures (gaierror) and everything else stay unclassified: a genuine
    500, not a warm-up.
    """
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, socket.gaierror):
            return False
        if isinstance(e, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
            return True
        sqlstate = getattr(e, "sqlstate", None)
        if isinstance(sqlstate, str) and sqlstate[:2] in ("08", "57"):
            return True
        e = e.__cause__ or e.__context__
    return False
