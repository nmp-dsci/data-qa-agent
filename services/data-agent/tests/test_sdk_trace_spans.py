"""s49 M0 — the child spans SdkTrace opens under the run's span.

Unlike ``test_otlp.py``, which fakes the tracer, these drive the REAL OTel SDK
with an in-memory exporter: the point of the milestone is that a run shows up as
a span waterfall in MLflow, and only a real tracer proves the spans are actually
created, named and attributed rather than merely that a fake was called.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from opentelemetry import trace as ot_trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent import otlp
from agent.sdk_trace import SdkTrace


@dataclass
class _ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class _TextBlock:
    text: str


@dataclass
class AssistantMessage:  # noqa: N801 — SdkTrace dispatches on the class NAME
    content: list[Any] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    model: str = "claude-sonnet-5"
    stop_reason: str | None = None


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """A real tracer whose spans land in memory, for the duration of one test."""
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setenv("OTLP_ENDPOINT", "http://localhost:5500")
    monkeypatch.setattr(ot_trace, "get_tracer", lambda name: provider.get_tracer(name))  # noqa: ARG005
    yield memory
    memory.clear()


def _named(exporter: InMemorySpanExporter, name: str) -> list[ReadableSpan]:
    return [s for s in exporter.get_finished_spans() if s.name == name]


def test_each_assistant_message_becomes_one_model_turn_span(
    exporter: InMemorySpanExporter,
) -> None:
    with otlp.agent_span("agent_sdk.answer"):
        trace = SdkTrace(system_prompt="sys", question="q", otel_parent=otlp.current_context())
        trace.consume(
            AssistantMessage(
                content=[_TextBlock("thinking out loud")],
                usage={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 2,
                },
                stop_reason="tool_use",
            )
        )
        trace.consume(
            AssistantMessage(
                content=[_ToolUseBlock("t0", "mcp__dp__extract", {"sql": "SELECT 1"})],
                usage={"input_tokens": 3, "output_tokens": 1},
            )
        )

    turns = _named(exporter, "model.turn")
    assert [s.attributes["turn"] for s in turns] == [1, 2]
    first = turns[0].attributes
    # input_tokens is the TOTAL input (uncached + cache read + cache write), the
    # convention the app's trace and cost tile use — see _usage_fields.
    assert first["input_tokens"] == 112
    assert first["output_tokens"] == 5
    assert first["cache_read"] == 100
    assert first["cache_write"] == 2
    assert first["stop_reason"] == "tool_use"
    assert first["model"] == "claude-sonnet-5"
    assert "tool_calls" not in first  # None attributes are dropped, not stringified
    assert turns[1].attributes["tool_calls"] == "mcp__dp__extract"


def test_turn_spans_nest_under_the_run_span(exporter: InMemorySpanExporter) -> None:
    with otlp.agent_span("agent_sdk.answer"):
        trace = SdkTrace(system_prompt="sys", question="q", otel_parent=otlp.current_context())
        trace.consume(AssistantMessage(content=[_TextBlock("hi")]))

    run = _named(exporter, "agent_sdk.answer")[0]
    turn = _named(exporter, "model.turn")[0]
    assert turn.parent is not None
    assert turn.parent.span_id == run.context.span_id
    assert turn.context.trace_id == run.context.trace_id


def test_no_spans_without_an_otlp_endpoint(
    exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole tracing path stays a true no-op when nothing is listening —
    the same contract agent_span has carried since s44."""
    monkeypatch.delenv("OTLP_ENDPOINT", raising=False)

    trace = SdkTrace(system_prompt="sys", question="q", otel_parent=otlp.current_context())
    trace.consume(AssistantMessage(content=[_TextBlock("hi")]))

    assert exporter.get_finished_spans() == ()
    # The flat trace is still built: tracing is an observer of the translation,
    # never a precondition for it.
    assert [e["kind"] for e in trace.entries] == ["system", "user", "model"]
