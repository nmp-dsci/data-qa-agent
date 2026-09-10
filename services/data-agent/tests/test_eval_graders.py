"""Unit tests for the deterministic eval graders (s14 E2)."""

from __future__ import annotations

from agent.eval_graders import (
    grade_extraction,
    grade_presentation_format,
    grade_ranked_set,
    grade_row_set,
    grade_scalar,
    grade_series,
    parse_scalar_number,
    reduce_scalar_actual,
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


def test_parse_scalar_number() -> None:
    assert parse_scalar_number("$718/wk") == 718.0
    assert parse_scalar_number("$1.25m") == 1_250_000.0
    assert parse_scalar_number("650k") == 650_000.0
    assert parse_scalar_number("12.5%") == 12.5
    assert parse_scalar_number("1,234.5") == 1234.5
    assert parse_scalar_number(718) == 718.0
    assert parse_scalar_number(None) is None
    assert parse_scalar_number("refused") is None


def test_reduce_scalar_manifest_kpi_takes_the_last_slide() -> None:
    # s48: the agent's deck carried a stale KPI slide, then a "Correction: ..."
    # slide — manifest_kpi must take the deck's current word, not its first.
    artifact = {
        "slides": [
            {"index": 0, "spec": {"headline": "Cover"}},
            {"index": 1, "spec": {"kpi": "$650/wk"}},
            {"index": 2, "spec": {"kpi": "$718/wk"}},
        ]
    }
    out = reduce_scalar_actual(
        golden_rows=[{"median_weekly_rent": 718.0}],
        actual_rows=[{"month": "2026-04", "median_weekly_rent": 700.0}] * 3,
        artifact=artifact,
    )
    assert out == {"value": 718.0, "scalar_source": "manifest_kpi"}


def test_reduce_scalar_manifest_kpi_reads_top_level_kpi_field() -> None:
    artifact = {"slides": [{"index": 0, "kpi": "$999"}]}
    out = reduce_scalar_actual(golden_rows=[{"v": 999.0}], actual_rows=[], artifact=artifact)
    assert out == {"value": 999.0, "scalar_source": "manifest_kpi"}


def test_reduce_scalar_key_match_joins_on_shared_columns() -> None:
    golden_rows = [{"month": "2026-05", "median_weekly_rent": 718.0}]
    actual_rows = [
        {"month": "2026-03", "median_weekly_rent": 690.0},
        {"month": "2026-05", "median_weekly_rent": 718.0},
        {"month": "2026-04", "median_weekly_rent": 700.0},
    ]
    out = reduce_scalar_actual(
        golden_rows=golden_rows, actual_rows=actual_rows, value="median_weekly_rent"
    )
    assert out == {"value": 718.0, "scalar_source": "key_match"}


def test_reduce_scalar_key_match_excludes_the_value_column() -> None:
    # A single-column golden row ({"median_weekly_rent": 718.0}) must not treat
    # its own value as a join key — that would key-match on the answer itself.
    golden_rows = [{"median_weekly_rent": 718.0}]
    actual_rows = [
        {"month": "2026-01", "median_weekly_rent": 650.0},
        {"month": "2026-05", "median_weekly_rent": 718.0},
    ]
    out = reduce_scalar_actual(golden_rows=golden_rows, actual_rows=actual_rows)
    # No key columns survive exclusion, so this falls through to last_row —
    # not a spurious key_match on median_weekly_rent == median_weekly_rent.
    assert out == {"value": 718.0, "scalar_source": "last_row", "reduced_from": 2}


def test_reduce_scalar_last_row_for_chronological_series() -> None:
    actual_rows = [{"median_weekly_rent": v} for v in (650.0, 680.0, 718.0)]
    out = reduce_scalar_actual(golden_rows=[{"median_weekly_rent": 718.0}], actual_rows=actual_rows)
    assert out == {"value": 718.0, "scalar_source": "last_row", "reduced_from": 3}


def test_reduce_scalar_first_row_when_actual_is_a_single_row() -> None:
    out = reduce_scalar_actual(golden_rows=[[718.0]], actual_rows=[[718.0]])
    assert out == {"value": 718.0, "scalar_source": "first_row"}
    empty = reduce_scalar_actual(golden_rows=[], actual_rows=[])
    assert empty == {"value": None, "scalar_source": "first_row"}


def test_reduce_scalar_explicit_reduce_overrides_precedence() -> None:
    artifact = {"slides": [{"index": 0, "spec": {"kpi": "$999"}}]}
    actual_rows = [{"v": 1.0}, {"v": 2.0}, {"v": 3.0}]
    golden_rows = [{"v": 3.0}]

    assert reduce_scalar_actual(
        golden_rows=golden_rows, actual_rows=actual_rows, artifact=artifact, reduce="first_row"
    ) == {"value": 1.0, "scalar_source": "first_row"}
    assert reduce_scalar_actual(
        golden_rows=golden_rows, actual_rows=actual_rows, artifact=artifact, reduce="last_row"
    ) == {"value": 3.0, "scalar_source": "last_row", "reduced_from": 3}
    assert reduce_scalar_actual(
        golden_rows=golden_rows, actual_rows=actual_rows, artifact=artifact, reduce="max"
    ) == {"value": 3.0, "scalar_source": "max"}
    assert reduce_scalar_actual(
        golden_rows=golden_rows, actual_rows=actual_rows, artifact=artifact, reduce="min"
    ) == {"value": 1.0, "scalar_source": "min"}
    # reduce="key_match" with no shared key columns besides "v" (excluded as
    # the sole golden column) finds no match rather than falling back.
    assert reduce_scalar_actual(
        golden_rows=golden_rows, actual_rows=actual_rows, artifact=artifact, reduce="key_match"
    ) == {"value": None, "scalar_source": "key_match"}


def test_grade_extraction_scalar_uses_reduction_and_reports_source() -> None:
    graded = grade_extraction(
        kind="scalar",
        golden_rows=[{"median_weekly_rent": 718.0}],
        actual_rows=[{"median_weekly_rent": v} for v in (650.0, 680.0, 718.0)],
        tolerance_pct=1.0,
    )
    assert graded == {
        "kind": "scalar",
        "score": 1.0,
        "scalar_source": "last_row",
        "reduced_from": 3,
    }


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
