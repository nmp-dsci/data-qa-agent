"""Turn a raw sandbox traceback into something a human — or the correcting
model — can act on.

Model-written analysis code fails inside the sandbox and comes back as a
``traceback.format_exc()`` string. Two kinds of consumer need that, and neither
wants the raw form:

* the object-codegen correction loop, which hands the failure back to the model
  for a repair pass — a targeted hint converts far more often than a bare
  ``TypeError`` line, and the correction budget is only two passes;
* every ``/agent/analysis*`` endpoint's ``error`` field (including the Golden
  Sandbox builder, which used to print the whole traceback into the status
  line where the success message belongs), so a UI can never show a bare
  traceback.

`HINTS` encodes the failure modes that come from the *shape of the house skills*
rather than from a typo, because those are the ones the model repeats. The
biggest is that several analysis skills return a dict: ``latest_value`` always
returns ``{"value", "month"}``, and ``growth_rate`` returns a ``{group: ...}``
mapping as soon as ``group_col`` is passed. Formatting one of those directly —
``f"{skills.latest_value(...):,.0f}"`` — raises ``unsupported format string
passed to dict.__format__``, which says nothing about the actual mistake.
"""

from __future__ import annotations

import re

__all__ = ["explain_sandbox_error", "final_exception_line"]

# (pattern on the final exception line, hint appended for the model/curator)
HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"unsupported format string passed to (dict|Series)\.__format__"),
        "A format spec was applied to a dict/Series. Several skills return a mapping, "
        "not a number: skills.latest_value(...) always returns {'value': float, "
        "'month': 'YYYY-MM'}, and skills.growth_rate(...) returns {group: ...} "
        "whenever group_col is passed. Index it before formatting — "
        "f\"{skills.latest_value(...)['value']:,.0f}\", not "
        'f"{skills.latest_value(...):,.0f}".',
    ),
    (
        re.compile(r"KeyError: ['\"](?P<name>[^'\"]+)['\"]"),
        "That column is not in the extract. Add it to the SQL SELECT (keeping the "
        "existing columns and WHERE so the other objects still build) rather than "
        "renaming it in pandas.",
    ),
    (
        re.compile(r"NameError: name ['\"](?P<name>[^'\"]+)['\"] is not defined"),
        "Only `df`, `pd` and `skills` are in scope — nothing may be imported, and no "
        "helper survives between runs.",
    ),
    (
        re.compile(r"unsupported operand type|can only concatenate|must be real number"),
        "Check the operand types: a skill returning a dict or None (no history, or a "
        "zero base) cannot be used in arithmetic without unpacking it first.",
    ),
)


def final_exception_line(traceback_text: str) -> str:
    """The last non-empty line of a traceback — the exception and its message.

    Everything above it is sandbox frame noise (``<exec>``, ``<string>``) with no
    bearing on the model's mistake.
    """
    lines = [ln.strip() for ln in (traceback_text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    for line in reversed(lines):
        if not line.startswith(("File ", "Traceback")):
            return line
    return lines[-1]


def explain_sandbox_error(traceback_text: str | None) -> str:
    """A one-or-two sentence, actionable rendering of a sandbox failure.

    Returns the final exception line, plus a hint when the failure matches a
    known skill-shape trap. Safe on empty/None input and on strings that are
    already a plain message rather than a traceback.
    """
    if not traceback_text:
        return ""
    summary = final_exception_line(traceback_text)
    if not summary:
        return traceback_text.strip()
    for pattern, hint in HINTS:
        if pattern.search(summary):
            return f"{summary} — {hint}"
    return summary
