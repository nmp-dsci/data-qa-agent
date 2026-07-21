"""Sandbox failures must come back as something a human or the correcting model
can act on — not a raw traceback.

The regression these guard is real: a Golden Sandbox build printed

    Traceback (most recent call last):
      File "<exec>", line 22, in <module>
      File "<string>", line 26, in <module>
    TypeError: unsupported format string passed to dict.__format__

into the builder's status line, where the success message belongs. The cause was
not a typo — several house skills return a mapping (``latest_value`` always;
``growth_rate`` once ``group_col`` is passed), so formatting one directly is a
mistake the model repeats.
"""

from __future__ import annotations

import pandas as pd

from agent.sandbox import explain_sandbox_error, run_code
from agent.sandbox.errors import final_exception_line

DICT_FORMAT_TB = (
    "Traceback (most recent call last):\n"
    '  File "<exec>", line 22, in <module>\n'
    '  File "<string>", line 26, in <module>\n'
    "TypeError: unsupported format string passed to dict.__format__\n"
)


def test_final_exception_line_strips_sandbox_frames() -> None:
    assert (
        final_exception_line(DICT_FORMAT_TB)
        == "TypeError: unsupported format string passed to dict.__format__"
    )


def test_dict_format_error_names_the_skills_that_return_mappings() -> None:
    """The hint has to say *which* skills return dicts — the bare TypeError says
    nothing about the actual mistake, and the correction budget is two passes."""
    out = explain_sandbox_error(DICT_FORMAT_TB)
    assert "unsupported format string" in out
    assert "latest_value" in out
    assert "growth_rate" in out
    # The fix is shown, not just described.
    assert "['value']" in out
    # Sandbox frame noise is gone.
    assert "<exec>" not in out
    assert "Traceback" not in out


def test_series_format_error_gets_the_same_hint() -> None:
    tb = "TypeError: unsupported format string passed to Series.__format__"
    assert "latest_value" in explain_sandbox_error(tb)


def test_missing_column_points_at_the_sql_not_pandas() -> None:
    out = explain_sandbox_error("KeyError: 'area_band'")
    assert "area_band" in out
    assert "SQL" in out


def test_nameerror_explains_the_locked_namespace() -> None:
    out = explain_sandbox_error("NameError: name 'np' is not defined")
    assert "skills" in out


def test_unknown_errors_degrade_to_the_exception_line() -> None:
    assert (
        explain_sandbox_error("ValueError: something specific") == "ValueError: something specific"
    )


def test_empty_and_none_are_safe() -> None:
    assert explain_sandbox_error(None) == ""
    assert explain_sandbox_error("") == ""
    assert explain_sandbox_error("   \n  ") == ""


def test_plain_message_passes_through_unharmed() -> None:
    """Callers also hand it non-traceback strings (e.g. `revised extract failed: …`)."""
    msg = "revised extract failed: relation does not exist"
    assert explain_sandbox_error(msg) == msg


def test_end_to_end_the_real_mistake_produces_the_real_hint() -> None:
    """Reproduce the actual failure through the real sandbox, not a fixture.

    `latest_value` returns {'value','month'}; formatting it directly is what the
    model did in production. The sandbox must catch it and the explainer must
    turn it into the indexing hint.
    """
    df = pd.DataFrame(
        {
            "month": pd.date_range("2024-01-01", periods=8, freq="MS").strftime("%Y-%m-%d"),
            "avg_price": [100.0 + i for i in range(8)],
        }
    )
    code = (
        "lv = skills.latest_value(df, month_col='month', value_col='avg_price')\n"
        "result = skills.build_report(summary=f'{lv:,.0f}')\n"  # the bug
    )
    outcome = run_code(code, df=df, frames={"extract": df})
    assert outcome.error, "the sandbox should have surfaced the TypeError"
    explained = explain_sandbox_error(outcome.error)
    assert "latest_value" in explained
    assert "['value']" in explained


def test_end_to_end_the_corrected_form_runs_clean() -> None:
    """The hint's suggested fix has to actually work — otherwise it sends the
    model in a circle for its remaining correction pass."""
    df = pd.DataFrame(
        {
            "month": pd.date_range("2024-01-01", periods=8, freq="MS").strftime("%Y-%m-%d"),
            "avg_price": [100.0 + i for i in range(8)],
        }
    )
    code = (
        "lv = skills.latest_value(df, month_col='month', value_col='avg_price')\n"
        "result = skills.build_report(summary=f\"{lv['value']:,.0f}\")\n"
    )
    outcome = run_code(code, df=df, frames={"extract": df})
    assert not outcome.error, outcome.error
    assert outcome.report
