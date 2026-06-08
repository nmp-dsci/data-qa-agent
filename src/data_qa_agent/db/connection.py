"""psycopg 3 connection helper, driven by ``DATABASE_URL`` from settings.

Never hardcode the connection string — it comes from
``get_settings().database_url`` (default
``postgresql://postgres:postgres@localhost:5432/data_qa``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from data_qa_agent.config import get_settings


def database_url() -> str:
    """Return the configured Postgres connection string."""
    return get_settings().database_url


def connect(*, autocommit: bool = False) -> psycopg.Connection:
    """Open a psycopg 3 connection to the configured database.

    The caller owns the connection lifecycle. ``autocommit=False`` (the default)
    means each file is loaded inside an explicit transaction (§2.3).
    """
    return psycopg.connect(database_url(), autocommit=autocommit)


@contextmanager
def connection(*, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Context-managed connection that always closes."""
    conn = connect(autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()
