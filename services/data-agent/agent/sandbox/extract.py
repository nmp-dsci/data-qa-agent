"""Governed extract → a bounded, RLS-scoped DataFrame for the sandbox.

The one place the sandbox path touches the database. RLS, SELECT-only validation,
the statement timeout, and the row cap are all enforced by ``run_select`` (db.py)
before a single row reaches the sandbox — the sandbox itself gets only the frame,
never a connection. Filtering / pre-aggregation happen here in SQL so the frame
handed to pandas is already small and already authorised.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..db import run_select


async def extract(sql: str, *, user_id: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run a governed SELECT and return (DataFrame, raw result metadata).

    The raw result (sql, columns, rows, row_count) is kept so the run can record
    the governed query alongside the report, exactly as the SQL path does today.
    """
    result = await run_select(sql, user_id=user_id)
    frame = pd.DataFrame(result["rows"], columns=result["columns"])
    return frame, result
