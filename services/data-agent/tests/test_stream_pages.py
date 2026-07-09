"""Streaming pages (s10) — the plan/page frame contract.

Unit tests at the deps level (no DB, no model): the frames the sandbox agent
pushes onto the progress queue are exactly what the SSE endpoint relays —
one ``plan`` frame declaring the user's page slots, then a ``page`` frame per
finished page carrying validated Template Studio Page JSON, with planned-but-
unproduced pages skipped explicitly so the client's ghost slots always clear.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent.pages import PagesEnvelope, compose_summary_page, page_plan
from agent.sandbox_agent import _SbDeps


def _report() -> dict[str, Any]:
    return {
        "element_id": "report",
        "summary": "Median rent is $671/wk, up 6.1% YoY.",
        "headlines": [
            {
                "element_id": "headline:0",
                "label": "median rent",
                "value": "$671/wk",
                "basis": "6-mo rolling, 2026-05",
                "related": False,
                "query_ref": "Q1",
            }
        ],
        "insights": [],
        "profiles": [],
        "main_chart": None,
    }


def _deps_for(plan: str) -> tuple[_SbDeps, asyncio.Queue[dict[str, Any]]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    deps = _SbDeps(user_id="u1", progress=queue, user_plan=plan)
    slots = page_plan(plan=plan)
    deps.page_indexes = {s["kind"]: s["index"] for s in slots if s["status"] != "locked"}
    deps.emit_frame("plan", {"pages": slots})
    return deps, queue


def _drain(queue: asyncio.Queue[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def test_frame_order_plan_then_page_then_skipped() -> None:
    deps, queue = _deps_for("plus")
    page, _ = compose_summary_page(_report())
    assert page is not None
    deps.emit_page("summary", page)
    deps.emit_skipped_pages()  # insights never produced

    frames = _drain(queue)
    assert [f["event"] for f in frames] == ["plan", "page", "page"]

    plan_frame = frames[0]
    assert [(s["kind"], s["status"]) for s in plan_frame["pages"]] == [
        ("summary", "building"),
        ("insights", "planned"),
        ("opportunities", "locked"),
    ]

    page_frame = frames[1]
    assert page_frame["index"] == 1
    assert page_frame["status"] == "complete"
    # The streamed page is the exact Template Studio contract — it re-validates.
    PagesEnvelope(pages=[page_frame["page"]])

    skipped = frames[2]
    assert skipped == {"event": "page", "index": 2, "kind": "insights", "status": "skipped"}


def test_page_above_plan_is_never_streamed() -> None:
    deps, queue = _deps_for("free")
    page, _ = compose_summary_page(_report())
    assert page is not None
    deps.emit_page("summary", page)
    deps.emit_page("insights", page)  # not entitled — must be dropped
    deps.emit_skipped_pages()

    frames = _drain(queue)
    assert [f["event"] for f in frames] == ["plan", "page"]
    assert frames[1]["kind"] == "summary"
    locked = [s for s in frames[0]["pages"] if s["status"] == "locked"]
    assert [s["kind"] for s in locked] == ["insights", "opportunities"]


def test_retry_re_emit_replaces_page_without_duplicate_trace_step() -> None:
    deps, queue = _deps_for("plus")
    page, _ = compose_summary_page(_report())
    assert page is not None
    deps.emit_page("summary", page)
    deps.emit_page("summary", page)  # model retried pass 1 — frame re-emits

    frames = _drain(queue)
    assert [f["event"] for f in frames] == ["plan", "page", "page"]
    assert frames[1]["index"] == frames[2]["index"] == 1  # client replaces by index
    emits = [s for s in deps.steps if s["kind"] == "page_emit"]
    assert len(emits) == 1  # but the trace records one streamed page


def test_emit_is_noop_off_the_streaming_path() -> None:
    deps = _SbDeps(user_id="u1", progress=None, user_plan="plus")
    deps.page_indexes = {"summary": 1}
    page, _ = compose_summary_page(_report())
    assert page is not None
    deps.emit_frame("plan", {"pages": []})
    deps.emit_page("summary", page)
    deps.emit_skipped_pages()  # nothing raises, nothing recorded as emitted
    assert deps.pages_emitted == {}
    assert [s for s in deps.steps if s["kind"] == "page_emit"] == []
