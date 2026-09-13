"""scripts/eval_run.py — linking a case's agent trace to its MLflow runs (s50).

Network-free: `mlflow_client.api` is monkeypatched to record the REST call it
would have made, same "stub the boundary, run the real logic" style as
test_mlflow_registry.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import eval_run  # noqa: E402
import mlflow_client as mc  # noqa: E402


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, Any]]:
    seen: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(mc, "api", lambda m, p, body=None, **kw: seen.append((m, p, body)) or {})
    return seen


def test_link_traces_to_run_posts_the_rest_body(calls: list[tuple[str, str, Any]]) -> None:
    mc.link_traces_to_run(["tr-abc", "", "tr-def"], "run-1")
    assert calls == [
        ("POST", "traces/link-to-run", {"trace_ids": ["tr-abc", "tr-def"], "run_id": "run-1"})
    ]


def test_link_traces_to_run_skips_an_empty_list(calls: list[tuple[str, str, Any]]) -> None:
    mc.link_traces_to_run([], "run-1")
    assert calls == []


def test_link_case_traces_links_case_and_eval_runs(calls: list[tuple[str, str, Any]]) -> None:
    trace = "0123456789abcdef0123456789abcdef"
    eval_run.link_case_traces({"otel_trace_id": trace}, "case-run", "eval-run")
    assert [(c[2]["run_id"], c[2]["trace_ids"]) for c in calls] == [
        ("case-run", [f"tr-{trace}"]),
        ("eval-run", [f"tr-{trace}"]),
    ]


def test_link_case_traces_skips_missing_trace_or_run(calls: list[tuple[str, str, Any]]) -> None:
    eval_run.link_case_traces({"otel_trace_id": ""}, "case-run", "eval-run")
    eval_run.link_case_traces({"otel_trace_id": "abc"}, None, None)
    assert calls == []


def test_link_case_traces_soft_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(*a: Any, **k: Any) -> Any:
        raise mc.MlflowError("tracking server down")

    monkeypatch.setattr(mc, "api", _boom)
    eval_run.link_case_traces({"otel_trace_id": "abc"}, "case-run")  # must not raise
    assert "mlflow(link) · skipped" in capsys.readouterr().out
