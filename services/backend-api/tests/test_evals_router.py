"""Evaluations tab — the enriched per-case result shape (s50).

Handler-level, no database: the JOIN in ``get_eval_run`` is exercised by the
live stack; what is asserted here is the pure shaping — that every enrichment
key the drill-down reads is present (and nullable), and that the trace reducer
recovers SQL and sandbox passes from *both* persisted trace shapes:

* the Agent SDK path (s45), which condenses extracts into ``decision_log`` and
  leaves frame/purpose only in the ``extract`` tool-call args, and
* the older pydantic-ai loop, which appended raw ``kind: sql`` steps.

Older runs that predate all of this must still shape to a row of ``None``s
rather than raise — the tab renders "—", never a crash.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.routers.evals import _reduce_trace, _result_row

_ENRICHED_KEYS = {
    "otel_trace_id",
    "mlflow_run_id",
    "answer",
    "sql_text",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
    "latency_ms",
    "degraded",
    "artifact_deck_url",
    "artifact_sheet_url",
    "trace",
    "golden_answer",
    "label",
    "golden_sql",
    "golden_sandbox",
    "golden_checkpoints",
    "grader",
    "expectation",
}

_ROW_COLUMNS = [
    "case_key",
    "question",
    "dataset",
    "holdout",
    "tier",
    "passed",
    "notes",
    "query_run_id",
    "g1",
    "g2",
    "g3",
    "g4",
    "judge",
    "checkpoints",
    "g5",
    "otel_trace_id",
    "mlflow_run_id",
    "artifact_manifest",
    "sql_text",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
    "latency_ms",
    "qr_otel_trace_id",
    "artifact_deck_url",
    "artifact_sheet_url",
    "degraded",
    "trace",
    "answer",
    "golden_answer",
    "label",
    "golden_sql",
    "golden_sandbox",
    "golden_checkpoints",
    "grader",
    "expectation",
]


def _row(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = dict.fromkeys(_ROW_COLUMNS)
    values.update(case_key="k", question="q", dataset="nsw_rent", holdout=False)
    values.update(overrides)
    return SimpleNamespace(**values)


_SQL = "SELECT month, x FROM marts.property_rent WHERE postcode = '2250'"
_CODE_1 = 'lv = skills.latest_value(df, month_col="month")\nresult = skills.build_report(lv)\n'
_CODE_2 = "raise ValueError('DEBUG')\n"


def _sdk_trace() -> list[dict[str, Any]]:
    return [
        {"kind": "system", "content": "..."},
        {
            "kind": "model",
            "content": "",
            "tool_calls": [
                {
                    "name": "mcp__dp__extract",
                    "args": json.dumps({"name": "df", "sql": _SQL, "purpose": "series"}),
                }
            ],
            "input_tokens": 10,
        },
        {"kind": "tool_return", "name": "mcp__dp__extract", "content": "ok"},
        {
            "kind": "model",
            "content": "",
            "tool_calls": [
                {"name": "mcp__dp__run_analysis", "args": json.dumps({"code": _CODE_1})}
            ],
        },
        {
            "kind": "model",
            "content": "",
            "tool_calls": [
                {"name": "mcp__dp__run_analysis", "args": json.dumps({"code": _CODE_2})}
            ],
        },
        {
            "kind": "decision_log",
            "decisions": [
                {
                    "type": "sql",
                    "choice": "Q1",
                    "sql": _SQL,
                    "status": "success",
                    "row_count": 113,
                    "why": "why",
                },
                {"type": "skill", "choice": "latest_value", "status": "ok"},
            ],
        },
        {
            "kind": "analysis",
            "runtime": "pyodide",
            "ms": 9000,
            "skills_used": ["latest_value", "build_report"],
            "skill_gaps": [],
            "used_inline_math": False,
            "passes": [
                {
                    "code_sha": "aaa",
                    "runtime": "pyodide",
                    "status": "ok",
                    "ms": 5000,
                    "stdout": "x" * 5000,
                },
                {
                    "code_sha": "bbb",
                    "runtime": "pyodide",
                    "status": "error",
                    "ms": 4000,
                    "error": "Traceback ... ValueError: DEBUG",
                },
            ],
        },
    ]


def test_reduce_sdk_trace_recovers_sql_and_passes() -> None:
    reduced = _reduce_trace(_sdk_trace())

    assert reduced["sql"] == [
        {
            "sql": _SQL,
            "status": "success",
            "row_count": 113,
            "frame": "df",
            "purpose": "series",
            "error": None,
        }
    ]
    analysis = reduced["analysis"]
    assert analysis["runtime"] == "pyodide"
    assert analysis["skills_used"] == ["latest_value", "build_report"]
    p1, p2 = analysis["passes"]
    assert p1["code"] == _CODE_1
    assert p1["skills_used"] == ["build_report", "latest_value"]
    assert len(p1["stdout"]) == 2048  # clipped, not the whole thing
    assert p1["error"] is None
    assert p2["status"] == "error" and p2["code"] == _CODE_2
    assert p2["skills_used"] == []  # code known, no skills.* call in it
    assert "DEBUG" in p2["error"]


def test_reduce_legacy_trace_uses_raw_sql_steps() -> None:
    trace = [
        {
            "kind": "sql",
            "sql": _SQL,
            "status": "error",
            "error": "relation does not exist",
            "purpose": "p",
        },
        {"kind": "sql", "sql": _SQL, "status": "success", "row_count": 3, "frame": "df"},
    ]
    reduced = _reduce_trace(trace)
    assert [s["status"] for s in reduced["sql"]] == ["error", "success"]
    assert reduced["sql"][0]["error"] == "relation does not exist"
    assert reduced["sql"][1]["frame"] == "df"
    assert reduced["analysis"] is None


def test_reduce_tolerates_missing_or_malformed_trace() -> None:
    assert _reduce_trace(None) == {"sql": [], "analysis": None}
    assert _reduce_trace("not a list") == {"sql": [], "analysis": None}
    assert _reduce_trace(
        [{"kind": "model", "tool_calls": [{"name": "extract", "args": "{bad"}]}]
    ) == {
        "sql": [],
        "analysis": None,
    }


def test_result_row_carries_every_enrichment_key_and_nulls_for_old_runs() -> None:
    row = _result_row(_row())
    assert _ENRICHED_KEYS <= set(row)
    for key in _ENRICHED_KEYS - {"trace", "golden_checkpoints", "grader"}:
        assert row[key] is None, key
    assert row["trace"] == {"sql": [], "analysis": None}
    assert row["golden_checkpoints"] == {} and row["grader"] == {}
    assert row["judge"] == {} and row["checkpoints"] == {}


def test_result_row_prefers_eval_result_trace_id_and_numbers_cost() -> None:
    from decimal import Decimal

    row = _result_row(
        _row(
            otel_trace_id=None,
            qr_otel_trace_id="01abc",
            cost_usd=Decimal("0.143610"),
            trace=_sdk_trace(),
            golden_checkpoints={"sql": {"key_cols": ["month"]}},
            answer="The rent is $699.",
        )
    )
    assert row["otel_trace_id"] == "01abc"  # falls back to the query_run's id
    assert row["cost_usd"] == 0.14361 and isinstance(row["cost_usd"], float)
    assert row["answer"] == "The rent is $699."
    assert row["golden_checkpoints"] == {"sql": {"key_cols": ["month"]}}
    assert row["trace"]["sql"][0]["frame"] == "df"

    row = _result_row(_row(otel_trace_id="e-id", qr_otel_trace_id="q-id"))
    assert row["otel_trace_id"] == "e-id"
