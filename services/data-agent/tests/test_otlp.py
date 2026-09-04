"""agent_span (s44 M3b) — the manual OTel span the Agent SDK runtime opens
around each run, since nothing auto-instruments a driven-subprocess loop the
way logfire's pydantic-ai instrumentation does for the champion.

No real OTel SDK/exporter here: ``opentelemetry.trace.get_tracer`` is replaced
with a fake tracer that records what was asked of it, so these tests exercise
exactly what agent_span promises — a true no-op with OTLP_ENDPOINT unset, and
correct span/attribute plumbing when it is set — without a collector.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest

from agent import otlp


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}
        self.ended = False

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: BaseException) -> None:
        self.attributes["_exception"] = str(exc)


class _FakeTracer:
    """A real OTel tracer's ``start_as_current_span`` IS the context manager —
    the yielded span has no ``__enter__``/``__exit__`` of its own — so this
    mirrors that: the span is marked ``ended`` when the ``with`` block here
    exits, not by the span itself."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, _FakeSpan]] = []

    @contextlib.contextmanager
    def start_as_current_span(self, name: str) -> Iterator[_FakeSpan]:
        span = _FakeSpan()
        self.spans.append((name, span))
        try:
            yield span
        finally:
            span.ended = True


def test_agent_span_is_a_true_no_op_when_otlp_endpoint_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No endpoint configured must mean no tracer is even looked up — mirrors
    otlp_processors()'s own "no destination, do nothing" check exactly."""
    monkeypatch.delenv("OTLP_ENDPOINT", raising=False)

    from opentelemetry import trace as ot_trace

    def boom(name: str) -> Any:
        raise AssertionError("get_tracer must not be called when OTLP_ENDPOINT is unset")

    monkeypatch.setattr(ot_trace, "get_tracer", boom)

    with otlp.agent_span("agent_sdk.answer", question_length=5) as span:
        assert isinstance(span, otlp._NoOpSpan)
        span.set_attribute("num_turns", 3)  # must not raise
        span.record_exception(RuntimeError("x"))  # must not raise


def test_agent_span_is_a_no_op_for_blank_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTLP_ENDPOINT", "   ")
    with otlp.agent_span("agent_sdk.answer") as span:
        assert isinstance(span, otlp._NoOpSpan)


def test_agent_span_creates_a_real_span_with_creation_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry import trace as ot_trace

    fake_tracer = _FakeTracer()
    monkeypatch.setenv("OTLP_ENDPOINT", "http://localhost:5500")
    monkeypatch.setattr(ot_trace, "get_tracer", lambda name: fake_tracer)  # noqa: ARG005

    with otlp.agent_span("agent_sdk.answer", question_length=42, run_id="r1", model=None) as span:
        span.set_attribute("num_turns", 3)

    assert len(fake_tracer.spans) == 1
    name, recorded = fake_tracer.spans[0]
    assert name == "agent_sdk.answer"
    assert recorded.attributes["question_length"] == 42
    assert recorded.attributes["run_id"] == "r1"
    # None-valued creation attributes are dropped, not stringified.
    assert "model" not in recorded.attributes
    # Attributes set on the yielded span after creation land on the same span.
    assert recorded.attributes["num_turns"] == 3
    assert recorded.ended


def test_agent_span_tracer_uses_the_data_agent_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry import trace as ot_trace

    seen: dict[str, str] = {}
    fake_tracer = _FakeTracer()

    def fake_get_tracer(name: str) -> _FakeTracer:
        seen["name"] = name
        return fake_tracer

    monkeypatch.setenv("OTLP_ENDPOINT", "http://localhost:5500")
    monkeypatch.setattr(ot_trace, "get_tracer", fake_get_tracer)

    with otlp.agent_span("agent_sdk.answer"):
        pass

    assert seen["name"] == otlp._TRACER_NAME
