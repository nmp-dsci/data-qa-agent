"""Checkpoint scoring + deck outline rendering (s49 M2).

Checkpoints diagnose; they never gate. These tests pin the two properties that
make that safe: an unspecified checkpoint scores ``None`` (not 0, which would
read as a failure), and a specified one scores what it claims to.
"""

from __future__ import annotations

from agent.eval_graders import (
    checkpoint_analysis,
    checkpoint_deck,
    checkpoint_sql,
    render_deck_outline,
    score_checkpoints,
)

MANIFEST = {
    "slides": [
        {
            "index": 0,
            "layout": "Title + KPI",
            "headline": "Rents kept climbing",
            "rows": 0,
            "spec": {"kpi": "$718", "kpi_label": "Median Weekly Rent", "chart_type": ""},
        },
        {
            "index": 1,
            "layout": "Title + Chart",
            "headline": "Twelve-month trend",
            "rows": 24,
            "spec": {"chart_type": "line", "kpi": "", "kpi_label": ""},
        },
    ]
}


def test_checkpoint_sql_is_f1_over_key_tuples() -> None:
    golden = [{"postcode": "2077"}, {"postcode": "2113"}, {"postcode": "2250"}]
    # Two of three found, plus one the golden never named: recall 2/3,
    # precision 2/3 → F1 2/3.
    actual = [{"postcode": 2077}, {"postcode": "2113"}, {"postcode": "9999"}]
    score = checkpoint_sql(["postcode"], golden, actual)
    assert score["matched"] == 2  # "2077" and 2077 are the same postcode
    assert round(score["score"], 3) == 0.667
    # Numeric-looking keys compare at their value, so the missing tuple is
    # reported the way it was compared.
    assert score["missing"] == ["(2250.0,)"]


def test_checkpoint_sql_without_golden_rows_scores_none() -> None:
    """No ground truth is "not measured", never "the agent got it wrong"."""
    assert checkpoint_sql(["postcode"], [], [{"postcode": "2077"}])["score"] is None


def test_checkpoint_analysis_averages_the_two_containments() -> None:
    out = checkpoint_analysis(
        ["latest_value", "growth_rate"],
        ["rent_growth_pct"],
        skills_used=["latest_value", "build_report"],
        frame_columns=["postcode", "rent_growth_pct"],
    )
    # skills 1/2, columns 1/1 → 0.75
    assert out["score"] == 0.75
    assert out["missing_skills"] == ["growth_rate"]
    assert out["missing_cols"] == []


def test_checkpoint_analysis_unspecified_scores_none() -> None:
    assert checkpoint_analysis([], [], skills_used=["latest_value"])["score"] is None


def test_checkpoint_deck_reads_layout_and_kpi_label() -> None:
    hit = checkpoint_deck(["Title + Chart"], "median weekly rent", MANIFEST)
    assert hit["layout_hit"] is True
    assert hit["kpi_hit"] is True
    assert hit["score"] == 1.0

    miss = checkpoint_deck(["Two Charts"], "yield", MANIFEST)
    assert miss["score"] == 0.0


def test_score_checkpoints_skips_unspecified_stages() -> None:
    out = score_checkpoints(
        {"sql": {"key_cols": ["postcode"]}},
        golden_rows=[{"postcode": "2077"}],
        actual_rows=[{"postcode": "2077"}],
        manifest=MANIFEST,
    )
    assert set(out) == {"sql"}
    assert out["sql"]["score"] == 1.0


def test_score_checkpoints_without_a_spec_is_empty() -> None:
    """A golden with no checkpoints block must not fill the column with nulls."""
    assert score_checkpoints(None, golden_rows=[{"a": 1}], actual_rows=[{"a": 1}]) == {}


def test_render_deck_outline_names_layout_headline_kpi_and_chart() -> None:
    outline = render_deck_outline(MANIFEST)
    assert "slide 1 · Title + KPI · Rents kept climbing · kpi Median Weekly Rent=$718" in outline
    assert "chart line" in outline
    assert "table 24 rows" in outline


def test_render_deck_outline_of_nothing_is_empty() -> None:
    assert render_deck_outline(None) == ""
    assert render_deck_outline({"slides": []}) == ""
