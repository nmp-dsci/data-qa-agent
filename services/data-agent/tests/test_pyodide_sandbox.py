"""Tests for the hardened Pyodide/WASM sandbox (restructure Phase B).

These exercise the real Node + Pyodide executor, so they are skipped unless both
Node and the bundled pyodide (node_modules) are present — i.e. they run in the
image and on a dev box that ran ``npm install`` under agent/sandbox/, and skip on
a bare checkout. The Phase A subprocess tests (test_sandbox.py) always run.
"""

from __future__ import annotations

import shutil

import pandas as pd
import pytest

from agent.sandbox.pyodide_runner import _HOST_SCRIPT
from agent.sandbox.pyodide_runner import run_code as pyodide_run_code

_NODE = shutil.which("node")
_HAVE_PYODIDE = (_HOST_SCRIPT.parent / "node_modules" / "pyodide").exists()

pytestmark = pytest.mark.skipif(
    _NODE is None or not _HAVE_PYODIDE,
    reason="Pyodide sandbox needs Node + node_modules/pyodide (run `npm install` in agent/sandbox)",
)

_TREND_CODE = """
series = skills.trend_series(df, month_col="month", value_col="avg_price")
g = skills.growth_rate(df, month_col="month", value_col="avg_price", years=3)
latest = skills.latest_value(df, month_col="month", value_col="avg_price")
result = skills.build_report(
    summary="Prices rose over the period.",
    headlines=[{"label": "Latest", "value": f"${latest['value']}"}],
    insights=[skills.make_insight("Up", f"Grew {g}% over 3y.")],
    main_chart=skills.trend_chart(series, title="Avg price trend"),
)
"""


def _df(n: int = 60) -> pd.DataFrame:
    months = pd.date_range("2021-01-01", periods=n, freq="MS").strftime("%Y-%m-%d")
    return pd.DataFrame({"month": months, "avg_price": [100.0 + 10 * i for i in range(n)]})


def test_happy_path_builds_report_in_wasm():
    res = pyodide_run_code(_TREND_CODE, _df())
    assert res.ok, res.error
    assert res.report is not None
    assert res.report["summary"] == "Prices rose over the period."
    assert res.report["main_chart"]["mark"] == "line"
    for name in ("trend_series", "growth_rate", "latest_value", "trend_chart", "build_report"):
        assert name in res.skills_used


def test_model_error_is_returned_not_raised():
    res = pyodide_run_code("result = df.no_such_method()", _df())
    assert not res.ok
    assert res.error is not None


def test_missing_result_is_reported():
    res = pyodide_run_code("x = 1 + 1", _df())
    assert not res.ok
    assert "result" in (res.error or "")


def test_import_os_is_blocked_in_wasm():
    # __import__ is not in the sandbox builtins → the import fails before any
    # (emscripten) os call. Even if it didn't, WASM has no host syscalls.
    res = pyodide_run_code("import os\nresult = os.listdir('/')", _df())
    assert not res.ok


def test_open_is_blocked_in_wasm():
    res = pyodide_run_code("result = open('/etc/passwd').read()", _df())
    assert not res.ok


def test_skill_gap_flows_back():
    code = (
        "skills.skill_gap('seasonality_adjust', 'no skill yet')\n"
        "result = skills.build_report(summary='partial')"
    )
    res = pyodide_run_code(code, _df())
    assert res.ok
    assert [g.need for g in res.skill_gaps] == ["seasonality_adjust"]
