"""agent.trace_sampling — the root-span name → keep/drop table (s50).

Exercises the real OTel sampler object, not just the regex helper, so the
ParentBased wiring (children follow their parent; only roots are judged by
name) is what's asserted.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision

from agent.trace_sampling import is_noisy_root, noise_sampler


@pytest.mark.parametrize(
    ("name", "noisy"),
    [
        ("GET /metrics", True),
        ("GET /health", True),
        ("GET /health/db", True),
        ("OPTIONS *", True),
        ("OPTIONS /ask", True),
        ("POST /events", True),
        ("GET /events", True),
        ("POST /ask", False),
        ("POST /ask/stream", False),
        ("POST /agent/ask", False),
        ("GET /agent/version", False),
        ("GET /admin/events", False),
        ("GET /healthz-not-ours", False),
        ("POST /metrics", False),
        ("agent_sdk.answer", False),
    ],
)
def test_is_noisy_root(name: str, noisy: bool) -> None:
    assert is_noisy_root(name) is noisy


def test_sampler_drops_noisy_roots_and_keeps_the_rest() -> None:
    sampler = noise_sampler()
    assert sampler.should_sample(None, 1, "GET /health").decision is Decision.DROP
    assert sampler.should_sample(None, 1, "POST /ask").decision is Decision.RECORD_AND_SAMPLE


def test_children_follow_the_root_decision() -> None:
    """A child span named like noise inside a kept trace is kept; every child
    of a dropped root is dropped — so a whole healthcheck trace never exports."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=noise_sampler())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("POST /ask"):
        with tracer.start_as_current_span("GET /health"):
            pass
    with tracer.start_as_current_span("GET /health"):
        with tracer.start_as_current_span("db.query"):
            pass

    names = sorted(s.name for s in exporter.get_finished_spans())
    assert names == ["GET /health", "POST /ask"]
    assert not trace.get_current_span().get_span_context().is_valid
