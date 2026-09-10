"""``_sheet_values`` — a transient read failure must not read as a deletion
(s48 §7 review fix). ``scripts/handover_poll.py`` is not an installed package,
so it is loaded by file path; the module resolves its own ``agent`` import
from ``__file__`` regardless of who imports it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "handover_poll.py"


def _load_handover_poll() -> Any:
    spec = importlib.util.spec_from_file_location("handover_poll", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


handover_poll = _load_handover_poll()


class _FakeClient:
    """Raises ``GoogleApiError`` for named tabs, returns fixed rows otherwise."""

    def __init__(
        self, values: dict[str, list[list[Any]]], *, failing_tabs: set[str] = frozenset()
    ) -> None:
        self._values = values
        self._failing_tabs = failing_tabs
        self.ranges: list[str] = []

    async def read_values(self, spreadsheet_id: str, a1_range: str) -> list[list[Any]]:
        self.ranges.append(a1_range)
        tab = handover_poll._tab_of_range(a1_range)
        if tab in self._failing_tabs:
            raise handover_poll.GoogleApiError(f"{a1_range} -> 503: backend error")
        return self._values[tab]

    async def _call(self, method: str, url: str, *, params: dict[str, Any] | None = None) -> Any:
        return {"version": "3", "modifiedTime": "2026-01-01T00:00:00Z"}


class _FakeResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._row

    def all(self) -> list[dict[str, Any]]:
        return [self._row] if self._row else []


class _FakeConn:
    """Records every executed statement; ``SELECT`` on artifact_snapshots
    always reports "no prior baseline" so ``_poll_kind`` takes the
    first-observation path."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(getattr(stmt, "text", stmt))
        self.executed.append(sql)
        return _FakeResult(None)


def _manifest(*tabs: str) -> dict[str, Any]:
    return {"slides": [{"table_name": f"t_{tab}", "table_range": f"'{tab}'!A1:B4"} for tab in tabs]}


def test_sheet_values_reads_every_healthy_tab() -> None:
    client = _FakeClient({"Data": [["m", "v"], ["2025-01", 700]]})
    values, failed = asyncio.run(
        handover_poll._sheet_values(client, "s1", _manifest("Data"))  # type: ignore[arg-type]
    )
    assert values == {"Data": [["m", "v"], ["2025-01", 700]]}
    assert failed == set()


def test_sheet_values_reports_a_failed_tab_without_raising_or_dropping_others() -> None:
    client = _FakeClient(
        {"Data": [["m", "v"], ["2025-01", 700]]},
        failing_tabs={"Overflow"},
    )
    values, failed = asyncio.run(
        handover_poll._sheet_values(  # type: ignore[arg-type]
            client, "s1", _manifest("Data", "Overflow")
        )
    )
    assert values == {"Data": [["m", "v"], ["2025-01", 700]]}
    assert failed == {"Overflow"}


def test_tab_of_range_strips_the_quoting_a1_notation_requires() -> None:
    assert handover_poll._tab_of_range("'s01_data'!A1:B4") == "s01_data"
    assert handover_poll._tab_of_range("Data!A1:B4") == "Data"
    assert handover_poll._tab_of_range("A1:B4") == ""


def test_first_poll_read_failure_writes_no_baseline() -> None:
    # A run with no stored baseline yet whose only tab fails to read must not
    # have that empty read persisted as the permanent version-1 snapshot —
    # doing so would fabricate a table_rows_added event on the next successful
    # poll, once the real rows are readable again.
    client = _FakeClient({}, failing_tabs={"Data"})
    conn = _FakeConn()
    new_events, opened_at = asyncio.run(
        handover_poll._poll_kind(
            conn,  # type: ignore[arg-type]
            client,  # type: ignore[arg-type]
            run_id="r1",
            kind="sheet",
            url="https://docs.google.com/spreadsheets/d/sheet123/edit",
            manifest=_manifest("Data"),
        )
    )
    assert (new_events, opened_at) == (0, None)
    assert not any("INSERT INTO app.artifact_snapshots" in sql for sql in conn.executed)
