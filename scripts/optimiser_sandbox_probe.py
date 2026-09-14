#!/usr/bin/env python3
"""Run one candidate script in the real sandbox and report what it produced.

Invoked as a subprocess by ``scripts/optimiser_common.run_candidate_in_sandbox``
from ``services/data-agent`` (``uv run --extra llm python …``), because
``agent.sandbox`` needs pandas and the agent's settings — neither of which the
repo-root environment carries, and neither of which the offline optimiser should
drag in just to prove a candidate skill.

Protocol: JSON on stdin ``{code, rows, expect_cols}``, one JSON line on stdout
``{ok, error, stdout, frames, skills_used, missing_cols}``. ``ok`` means the
sandbox ran without an error AND every ``expect_cols`` column appeared on one of
the derived frames — i.e. the candidate actually computed what the golden's
``checkpoints.analysis.derived_cols`` says the answer needs.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

DATA_AGENT_DIR = Path(__file__).resolve().parents[1] / "services" / "data-agent"
if str(DATA_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_AGENT_DIR))


def main() -> int:
    payload: dict[str, Any] = json.loads(sys.stdin.read() or "{}")
    code: str = payload.get("code") or ""
    rows: list[dict[str, Any]] = payload.get("rows") or []
    expect_cols: list[str] = [str(c) for c in payload.get("expect_cols") or []]

    import pandas as pd  # type: ignore[import-untyped]  # noqa: PLC0415
    from agent.sandbox import run_code  # type: ignore[import-not-found]  # noqa: PLC0415

    # The sandbox contract is "assign a report dict to `result`" — a whole answer.
    # A candidate skill is only the maths, so when the test snippet has not built
    # a report we append a placeholder one: without it the run is rejected before
    # `capture_frames` ever sees the derived frames we are trying to inspect.
    if not re.search(r"^\s*result\s*=", code, re.MULTILINE):
        code += "\n\nresult = {'headline': 'optimiser candidate test', 'bullets': []}\n"

    df = pd.DataFrame(rows)
    result = run_code(code, df)

    frames = [
        {"name": f.get("name"), "columns": list(f.get("columns") or [])}
        for f in (result.frames or [])
    ]
    produced: set[str] = set()
    for frame in frames:
        produced.update(str(c) for c in frame["columns"])
    missing = [c for c in expect_cols if c not in produced]

    out = {
        # A candidate is a maths function, not a whole answer: it need not build
        # a report, so `ok` is "no error + produced the expected columns" rather
        # than AnalysisResult.ok (which also requires report is not None).
        "ok": result.error is None and not missing,
        "error": result.error,
        "stdout": getattr(result, "stdout", "") or "",
        "frames": frames,
        "skills_used": list(result.skills_used or []),
        "missing_cols": missing,
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
