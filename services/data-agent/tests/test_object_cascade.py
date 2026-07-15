"""Object-edit full cascade (s16).

Covers the guarantees behind "edit one object → SQL/sandbox/presentation update
together, and the golden stays reproducible":

* the run-book, replayed with NO LLM, reproduces every object byte-for-byte
  (the sign-off: a golden can be recreated from its stored SQL + code alone);
* ``_lift_target`` picks the edited object (by id, else by field-signature diff);
* the object digest marks the target and never leaks the (huge) row payload;
* the offline stub is NON-destructive — it keeps the existing run_analysis rather
  than clobbering the golden's other objects.

No DB or LLM: the sandbox runs over an in-memory frame and the stub path is
forced, so these are fast and deterministic.
"""

from __future__ import annotations

import asyncio

import pandas as pd

import agent.object_codegen as oc
from agent.main import _chart_sig, _lift_target
from agent.object_codegen import _objects_digest
from agent.pages import compose_pages
from agent.sandbox import run_code

# A multi-object run_analysis: a KPI headline + a bar main_chart + a trend insight
# chart — i.e. exactly the kind of golden whose OTHER objects the old single-object
# edit used to destroy.
_MULTI_CODE = """
s = skills.trend_series(df, month_col="month", value_col="avg_price", group_col="suburb")
trend = skills.trend_chart(s, title="Price trend")
band = df.groupby("area_band", as_index=False)["n_sold"].sum()
bar = skills.comparison_chart(band, category_col="area_band", value_col="n_sold")
latest = skills.latest_value(df, month_col="month", value_col="avg_price")
result = skills.build_report(
    summary="Trend and volume by band.",
    headlines=[{"label": "Latest avg", "value": f"${latest['value']}"}],
    main_chart=bar,
    insights=[{"heading": "Trend", "body": "rising", "chart": trend}],
)
"""


def _multi_df() -> pd.DataFrame:
    rows = []
    for suburb, bump in (("Normanhurst", 50_000), ("Hornsby", 0)):
        for i in range(12):
            rows.append(
                {
                    "month": f"2023-{i + 1:02d}-01",
                    "suburb": suburb,
                    "area_band": ["<400", "400-700", "700+"][i % 3],
                    "n_sold": 10 + i,
                    "avg_price": 500_000 + i * 1_000 + bump,
                }
            )
    return pd.DataFrame(rows)


def test_run_book_reproduces_every_object_without_llm() -> None:
    """The sign-off: re-running the STORED run_analysis over the same extract
    reproduces the identical report + every object — no agent, no LLM. This is what
    lets a golden be saved and recreated from SQL + Sandbox + Presentation alone."""
    df = _multi_df()
    first = run_code(_MULTI_CODE, df)
    second = run_code(_MULTI_CODE, df)
    assert first.ok, first.error
    assert second.ok, second.error
    # Deterministic run-book: same extract in → identical report out.
    assert first.report == second.report
    pages_a, _ = compose_pages(first.report or {})
    pages_b, _ = compose_pages(second.report or {})
    assert pages_a == pages_b
    types = {o["type"] for p in pages_a for col in p["columns"] for o in col}
    # Every object the golden presents is reproduced by the stored code.
    assert {"kpi", "breakdown", "trend"} <= types


def _pages_with(objs: list[dict]) -> list[dict]:
    return [{"template": "one-col", "columns": [objs]}]


def test_lift_target_by_element_id() -> None:
    pages = _pages_with(
        [
            {
                "element_id": "report:chart",
                "type": "compare",
                "data": {"dimension": "area_band", "measure": "n_sold"},
            }
        ]
    )
    got = _lift_target(pages, {}, "report:chart", [])
    assert got is not None and got["element_id"] == "report:chart"


def test_lift_target_signature_diff_picks_the_new_chart() -> None:
    """Adding a NEW chart (its element_id doesn't exist yet): the target is the
    chart whose fields aren't among the existing presentation charts."""
    pages = _pages_with(
        [
            {
                "element_id": "insight:0:chart",
                "type": "trend",
                "data": {"x": "month", "y": "avg_price", "series": "suburb"},
            },
            {
                "element_id": "report:chart",
                "type": "breakdown",
                "data": {"dimension": "area_band", "measure": "n_sold"},
            },
        ]
    )
    existing = [
        {
            "element_id": "report:chart",
            "type": "trend",
            "data": {"x": "month", "y": "avg_price", "series": "suburb"},
        }
    ]
    got = _lift_target(pages, {}, "edit:compare:new", existing)
    assert got is not None and got["type"] == "breakdown"  # the new bar, not the pre-existing trend


def test_lift_target_none_when_no_new_chart() -> None:
    """A run that only reproduced the pre-existing charts (didn't honour the edit)
    yields no target, so the caller surfaces an error rather than a stale duplicate."""
    pages = _pages_with(
        [
            {
                "element_id": "report:chart",
                "type": "trend",
                "data": {"x": "month", "y": "avg_price", "series": "suburb"},
            }
        ]
    )
    existing = [
        {
            "element_id": "prev",
            "type": "trend",
            "data": {"x": "month", "y": "avg_price", "series": "suburb"},
        }
    ]
    assert _lift_target(pages, {}, "edit:new", existing) is None


def test_chart_sig_bar_and_trend_shapes() -> None:
    assert _chart_sig({"dimension": "a", "measure": "b", "group": "g"}) == ("a", "b", "", "g")
    assert _chart_sig({"x": "month", "y": "v", "series": "s"}) == ("month", "v", "", "s")


def test_objects_digest_marks_target_and_hides_rows() -> None:
    objs = [
        {
            "element_id": "e1",
            "type": "compare",
            "role": "chart",
            "_target": True,
            "data": {"dimension": "area_band", "measure": "n_sold", "rows": [{"a": 1}] * 500},
        }
    ]
    out = _objects_digest(objs)
    assert "target←EDIT THIS" in out  # the model is told which object to change
    assert "area_band" in out and "n_sold" in out  # the fields it needs to preserve
    assert "rows" not in out and '"a": 1' not in out  # the huge payload never ships


def test_stub_is_non_destructive(monkeypatch) -> None:
    """No LLM key → the stub keeps the existing run_analysis intact (never clobbers
    the golden's other objects) and proposes no SQL change."""
    monkeypatch.setattr(oc, "choose_provider", lambda *a, **k: None)
    code = (
        "result = skills.build_report(headlines=[{'label': 'K', 'value': '1'}], "
        "main_chart=skills.comparison_chart(df, category_col='area_band', value_col='n_sold'))"
    )
    out = asyncio.run(
        oc.scaffold_object(
            instruction="change the bars to total value",
            object_type="compare",
            columns=["area_band", "n_sold"],
            code=code,
            sql="SELECT area_band, n_sold FROM t",
        )
    )
    assert out["engine"] == "stub"
    assert out["sql"] is None
    assert out["code"] == code  # non-destructive: the existing code is preserved
