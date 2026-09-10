"""GET /analytics/handover (s48 §7) — handler-level, mirroring
test_ops_rollup.py's fake-connection style so this needs no database.

Pins two things: the endpoint reads through ``admin_ro_connection`` (every
other analytics aggregate does, because the rollup spans every user's runs,
which no single RLS context can see), and it assembles the §7 response shape
correctly from what the queries return — including the "no data yet" cases
(empty window, no edited decks) reading as ``0``/``None`` rather than raising.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.auth import CurrentUser
from app.routers import analytics


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def scalar(self) -> Any:
        return self._rows[0][next(iter(self._rows[0]))] if self._rows else None

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConn:
    """Dispatches on a recognisable fragment of the SQL text — the queries in
    analytics_handover are each distinguishable this way, and it lets the test
    assert on inputs/outputs without caring about exact SQL formatting."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        # In insertion order: several of these queries share the window
        # clause as a literal *prefix* (each adds one more AND on top of it),
        # so the caller must register the more specific fragments (the ones
        # naming the extra AND) before the generic "just the window" one, or
        # the generic fragment — being a substring of all of them — wins first.
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return _FakeResult(rows)
        raise AssertionError(f"no fake answer registered for: {sql[:120]}")


class _FakeConnCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000000", username="admin", email="a@x.test", role="admin"
    )


def _answers(**overrides: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    # Order matters — see _FakeConn.execute: the more specific fragments (the
    # ones naming what's appended on top of the shared window clause) must be
    # registered before the generic "just the window" one.
    base: dict[str, list[dict[str, Any]]] = {
        "artifact_opened_at IS NOT NULL": [{"count": 9}],
        "artifact_edit_count > 0": [{"count": 5}],
        "percentile_cont(0.5)": [{"percentile_cont": 42.5}],
        "ae.event, count(*) AS edits": [
            {"event": "headline_changed", "edits": 4},
            {"event": "chart_type_changed", "edits": 2},
        ],
        "ae.layout_id, count(DISTINCT ae.run_id)": [
            {"layout_id": "L2", "decks": 3, "edits": 5, "headline_edits": 3, "chart_edits": 2}
        ],
        "coalesce(s->>'layout_id', s->>'layout')": [{"layout_id": "L2", "slides": 7}],
        "qr.artifact_deck_url AS deck_url": [
            {
                "run_id": "r1",
                "question": "rent trend?",
                "deck_url": "https://docs.google.com/presentation/d/abc/edit",
                "edits": 2,
                "last_edit_at": None,
            }
        ],
        "count(*) FROM app.query_runs WHERE artifact_deck_url": [{"count": 12}],
    }
    base.update(overrides)
    return base


@pytest.fixture
def patched_admin_ro(monkeypatch: pytest.MonkeyPatch):
    def _patch(answers: dict[str, list[dict[str, Any]]]) -> _FakeConn:
        conn = _FakeConn(answers)
        monkeypatch.setattr(analytics, "admin_ro_connection", lambda: _FakeConnCtx(conn))
        return conn

    return _patch


async def test_handover_reads_through_admin_ro(admin: CurrentUser, patched_admin_ro: Any) -> None:
    conn = patched_admin_ro(_answers())
    await analytics.analytics_handover(days=30, admin=admin)
    # Every query in this rollup spans every user's runs, so all of them must
    # go through admin_ro — none of the ops_rollup-style RLS-context pitfalls.
    joined = " ".join(conn.statements)
    assert "app.query_runs" in joined
    assert "app.artifact_edits" in joined


async def test_handover_response_shape(admin: CurrentUser, patched_admin_ro: Any) -> None:
    patched_admin_ro(_answers())
    result = await analytics.analytics_handover(days=30, admin=admin)

    assert result["decks"] == 12
    assert result["opened"] == 9
    assert result["edited"] == 5
    assert result["edit_rate"] == pytest.approx(5 / 12)
    assert result["median_minutes_to_first_edit"] == pytest.approx(42.5)
    assert result["edits_by_event"][0]["event"] == "headline_changed"
    assert result["edits_by_layout"][0]["layout_id"] == "L2"
    assert result["layout_usage"][0]["slides"] == 7
    assert result["recent"][0]["run_id"] == "r1"


async def test_handover_empty_window_reads_as_zero_not_error(
    admin: CurrentUser, patched_admin_ro: Any
) -> None:
    patched_admin_ro(
        _answers(
            **{
                "count(*) FROM app.query_runs WHERE artifact_deck_url": [{"count": 0}],
                "artifact_opened_at IS NOT NULL": [{"count": 0}],
                "artifact_edit_count > 0": [{"count": 0}],
                "percentile_cont(0.5)": [{"percentile_cont": None}],
                "ae.event, count(*) AS edits": [],
                "ae.layout_id, count(DISTINCT ae.run_id)": [],
                "coalesce(s->>'layout_id', s->>'layout')": [],
                "qr.artifact_deck_url AS deck_url": [],
            }
        )
    )
    result = await analytics.analytics_handover(days=30, admin=admin)
    assert result["decks"] == 0
    assert result["edit_rate"] is None  # never a false "0%" over no decks
    assert result["median_minutes_to_first_edit"] is None
    assert result["edits_by_event"] == []
    assert result["recent"] == []
