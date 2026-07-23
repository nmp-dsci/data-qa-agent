"""s29: Aurora-wake classification (pure half — no backend stack needed).

While Aurora Serverless resumes from auto-pause, every connect fails; in prod
that read as a dead login button and 4-5 sign-in attempts. The classifier below
decides which failures the API may call "db_warming" (a retryable 503 the
frontend login flow waits out) — misclassifying a real error as a warm-up would
hide genuine outages behind a soothing message, so the negative cases matter as
much as the positives. The 503 handler and /health/db endpoint are covered in
services/backend-api/tests/, where fastapi is installed.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "backend-api"))

from app.waking import is_db_waking  # noqa: E402


def _wrapped_in(outer: Exception, inner: BaseException) -> Exception:
    """Chain `inner` under `outer` the way SQLAlchemy wraps driver errors."""
    try:
        try:
            raise inner
        except BaseException as caught:
            raise outer from caught
    except Exception as chained:
        return chained


class _SqlstateError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def test_connect_phase_failures_classify_as_waking() -> None:
    assert is_db_waking(ConnectionRefusedError(61, "Connection refused"))
    assert is_db_waking(TimeoutError())  # bounded connect (db.py timeout=5)
    # SQLSTATE class 57 (57P03 "the database system is starting up") and 08
    # (connection exception) — how asyncpg reports a resuming server.
    assert is_db_waking(_SqlstateError("57P03"))
    assert is_db_waking(_SqlstateError("08006"))


def test_wrapped_driver_errors_are_found_through_the_chain() -> None:
    assert is_db_waking(_wrapped_in(RuntimeError("sqlalchemy wrapper"), ConnectionRefusedError()))
    assert is_db_waking(_wrapped_in(RuntimeError("sqlalchemy wrapper"), _SqlstateError("57P03")))


def test_real_errors_do_not_classify_as_waking() -> None:
    assert not is_db_waking(ValueError("boom"))
    assert not is_db_waking(_SqlstateError("42P01"))  # undefined_table: a real bug
    # DNS failure is misconfiguration, not a warm-up — retrying can't fix it.
    assert not is_db_waking(socket.gaierror(8, "nodename nor servname provided"))
    assert not is_db_waking(_wrapped_in(RuntimeError("wrapper"), ValueError("boom")))
