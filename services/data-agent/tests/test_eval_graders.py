"""Unit tests for the deterministic eval graders (s14 E2)."""

from __future__ import annotations

from agent.eval_graders import (
    grade_extraction,
    grade_presentation_format,
    grade_ranked_set,
    grade_row_set,
    grade_scalar,
    grade_series,
    within_tolerance,
)


def test_within_tolerance_relative_and_zero() -> None:
    assert within_tolerance(100, 100.5, 1.0)
    assert not within_tolerance(100, 105, 1.0)
    assert within_tolerance(0, 0.005, 1.0)  # absolute fallback at zero
    assert not within_tolerance(0, 0.5, 1.0)


def test_grade_scalar() -> None:
    assert grade_scalar(100, 100.5, tolerance_pct=1.0) == 1.0
    assert grade_scalar(100, 105, tolerance_pct=1.0) == 0.0
    assert grade_scalar("refused", "refused") == 1.0  # non-numeric equality
    assert grade_scalar("refused", "answered") == 0.0


def test_grade_row_set_f1() -> None:
    golden = [{"suburb": "A"}, {"suburb": "B"}, {"suburb": "C"}]
    actual = [{"suburb": "A"}, {"suburb": "B"}, {"suburb": "D"}]
    # tp=2, precision=2/3, recall=2/3 → F1 = 2/3
    assert abs(grade_row_set(golden, actual, key="suburb") - 2 / 3) < 1e-9
    assert grade_row_set([], [], key="suburb") == 1.0
    assert grade_row_set(golden, [{"suburb": "Z"}], key="suburb") == 0.0


def test_grade_ranked_set_topk() -> None:
    golden = [{"s": x} for x in ["A", "B", "C", "D", "E"]]
    actual = [{"s": x} for x in ["A", "B", "C", "D", "Z"]]
    assert grade_ranked_set(golden, actual, key="s", k=5) == 0.8
    assert grade_ranked_set(golden, golden, key="s", k=5) == 1.0


def test_grade_series_pointwise() -> None:
    golden = [{"m": "2024-01", "v": 100}, {"m": "2024-02", "v": 200}]
    actual = [{"m": "2024-01", "v": 100.5}, {"m": "2024-02", "v": 260}]
    # point 1 within 1%, point 2 not → 0.5
    assert grade_series(golden, actual, key="m", value="v", tolerance_pct=1.0) == 0.5


def test_grade_extraction_dispatch() -> None:
    assert (
        grade_extraction(
            kind="scalar", golden_rows=[[5.0]], actual_rows=[[5.02]], tolerance_pct=1.0
        )["score"]
        == 1.0
    )
    ranked = grade_extraction(
        kind="ranked_set",
        golden_rows=[{"s": "A"}, {"s": "B"}],
        actual_rows=[{"s": "A"}, {"s": "B"}],
        key="s",
        k=2,
    )
    assert ranked["score"] == 1.0
    bogus = grade_extraction(kind="bogus", golden_rows=[], actual_rows=[])
    assert bogus["score"] == 0.0 and "error" in bogus


def test_grade_presentation_format() -> None:
    good = {
        "summary": "Gosford leads on yield.",
        "queries": [{"ref": "q1"}],
        "knowledge_version": "kv-a",
        "pages": [{"columns": [[{"type": "trend"}]]}],
    }
    ok = grade_presentation_format(good, expected_objects=["trend"])
    assert ok["passed"] is True and ok["issues"] == []

    missing = grade_presentation_format(good, expected_objects=["compare"])
    assert missing["passed"] is False
    assert any("compare" in i for i in missing["issues"])

    empty = grade_presentation_format(None)
    assert empty["passed"] is False
