"""The ops rollup reads across users, and reads through the right role (s32 W0).

This exists because of a bug found in review, and the bug's shape is the reason a
test is warranted rather than a comment.

``rls_connection(None)`` sets an EMPTY RLS context, so ``app.current_user_id()``
is NULL and every RLS-protected table — ``query_runs``, ``messages``, ``events``,
``answer_feedback`` — returns **zero rows**. It does not error. So the first
version of the background refresh happily computed a rollup of all zeros and
upserted it over a good one: open ``/ops`` five minutes after a real refresh and
the deck would blank itself, with nothing in the logs.

The fix is that the aggregate reads through ``admin_ro`` (BYPASSRLS, SELECT-only,
migration 0012) and only the *write* uses the ordinary connection. That is an
invariant no type checker can hold, and a mistake nobody would notice from the
outside, so it is asserted here — with fakes, so the test needs no database and
runs in CI.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app import ops_rollup


class _FakeConn:
    """Records what was executed, and answers everything with empty scalars."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append(str(statement))
        return _FakeResult()


class _FakeResult:
    """Enough of a SQLAlchemy result for the aggregate helpers to run."""

    def one(self) -> Any:
        return _FakeRow()

    def scalar(self) -> Any:
        return 0

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> Any:
        return None

    def all(self) -> list[Any]:
        return []

    def __iter__(self) -> Any:
        return iter(())


class _FakeRow:
    """Any column access yields 0, which is what the formatters tolerate."""

    def __getattr__(self, _name: str) -> int:
        return 0


class _FakeConnCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


def test_refresh_reads_through_admin_ro_and_writes_through_app_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # asyncio.run rather than pytest-asyncio: this service's test deps are
    # deliberately minimal, and one await does not justify a plugin.
    read = _FakeConn("admin_ro")
    write = _FakeConn("app_user")
    rls_calls: list[str | None] = []

    def fake_admin_ro() -> _FakeConnCtx:
        return _FakeConnCtx(read)

    def fake_rls(user_id: str | None) -> _FakeConnCtx:
        rls_calls.append(user_id)
        return _FakeConnCtx(write)

    monkeypatch.setattr(ops_rollup, "admin_ro_connection", fake_admin_ro)
    monkeypatch.setattr(ops_rollup, "rls_connection", fake_rls)

    asyncio.run(ops_rollup.refresh_window("24h"))

    # Every aggregate — the ones over RLS-protected tables especially — must have
    # gone through admin_ro. If any of them were on the RLS connection they would
    # silently return zero rows for every user's data.
    read_sql = " ".join(read.statements).lower()
    for table in ("app.query_runs", "app.messages", "app.events", "app.answer_feedback"):
        assert table in read_sql, f"{table} was not read through admin_ro"
    assert "percentile_cont" in read_sql

    # …and the ONLY thing on the ordinary connection is the rollup upsert, which
    # admin_ro cannot do (SELECT-only) and which needs no RLS context because
    # app.ops_rollup has no policies.
    assert len(write.statements) == 1
    written = write.statements[0].lower()
    assert "insert into app.ops_rollup" in written
    assert rls_calls == [None]


def test_every_window_is_refreshed(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshed: list[str] = []

    async def fake_refresh(window_key: str) -> dict[str, Any]:
        refreshed.append(window_key)
        return {}

    monkeypatch.setattr(ops_rollup, "refresh_window", fake_refresh)
    assert asyncio.run(ops_rollup.refresh_all()) == list(ops_rollup.WINDOWS)
    assert refreshed == list(ops_rollup.WINDOWS)


def test_slo_lamp_states_are_honest() -> None:
    """A lamp must never read green on missing data — "off" is not "on"."""
    # No data at all: off, not a false pass.
    assert ops_rollup._slo_state(None, 0.99, higher_is_better=True) == "off"
    # Availability: at or above target is on; just under warns; well under is bad.
    assert ops_rollup._slo_state(0.995, 0.99, higher_is_better=True) == "on"
    assert ops_rollup._slo_state(0.985, 0.99, higher_is_better=True) == "warn"
    assert ops_rollup._slo_state(0.90, 0.99, higher_is_better=True) == "bad"
    # Latency: lower is better, so the comparison inverts.
    assert ops_rollup._slo_state(2500, 3000, higher_is_better=False) == "on"
    assert ops_rollup._slo_state(3200, 3000, higher_is_better=False) == "warn"
    assert ops_rollup._slo_state(48000, 3000, higher_is_better=False) == "bad"


def test_rates_never_divide_by_zero() -> None:
    # An empty window is the normal state of a fresh deployment, so every rate has
    # to answer "no data" rather than raise or report 0% (which reads as healthy).
    assert ops_rollup._rate(0, 0) is None
    assert ops_rollup._rate(None, 10) is None
    assert ops_rollup._rate(3, 12) == 0.25
