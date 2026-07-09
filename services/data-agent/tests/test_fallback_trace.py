"""Fallback trace salvage — the LLM's work stays visible when we fall to stub.

When the sandbox agent runs but never completes a report, answer_with_sandbox
returns a salvage dict instead of None; main._answer prepends the salvaged
trace (model turns with input/output, tool calls) to the stub answer's steps
and carries the token consumption. Regression for "the agent run stopped
showing input/output, tool calls and tokens" on stub-fallback answers.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent import main
from agent.main import AskRequest, UserCtx
from agent.sandbox_agent import _salvage_fallback


class _FakeDeps:
    steps: list[dict[str, Any]] = []


def test_salvage_fallback_shape() -> None:
    salvage = _salvage_fallback([], _FakeDeps(), "budget spent")
    assert salvage["fallback"] is True
    fb = salvage["steps"][-1]
    assert fb["kind"] == "fallback"
    assert fb["error"] == "budget spent"
    assert fb["to"] == "stub"


def test_answer_keeps_salvaged_trace_on_stub_fallback(monkeypatch) -> None:
    salvage = {
        "fallback": True,
        "steps": [
            {"kind": "system", "content": "…"},
            {"kind": "user", "content": "q"},
            {
                "kind": "model",
                "content": "thinking…",
                "input_tokens": 12000,
                "output_tokens": 340,
                "tool_calls": [{"name": "run_sql", "args": "{}"}],
            },
            {"kind": "fallback", "status": "error", "error": "budget spent", "to": "stub"},
        ],
        "input_tokens": 12000,
        "output_tokens": 340,
    }

    async def fake_sandbox(question: str, **kwargs: Any) -> dict[str, Any]:
        return salvage

    async def fake_run_select(sql: str, *, user_id: str) -> dict[str, Any]:
        return {"sql": sql, "columns": ["c"], "rows": [[1]], "row_count": 1}

    monkeypatch.setattr(main, "answer_with_sandbox", fake_sandbox)
    monkeypatch.setattr(main, "run_select", fake_run_select)

    body = AskRequest(
        question="How many suburbs do we have?",
        user=UserCtx(id="00000000-0000-0000-0000-000000000001", role="user"),
    )
    out = asyncio.run(main._answer(body))

    assert out.engine == "stub"
    # Token consumption from the failed LLM run is carried onto the answer.
    assert out.input_tokens == 12000
    assert out.output_tokens == 340
    # The salvaged trace leads the steps, then the stub's own sql step.
    kinds = [s["kind"] for s in out.steps]
    assert kinds[:4] == ["system", "user", "model", "fallback"]
    assert "sql" in kinds[4:]
    model = out.steps[2]
    assert model["tool_calls"][0]["name"] == "run_sql"
