"""Claude Agent SDK message stream → the app's flat trace + usage totals (M1).

The champion (pydantic-ai) flattens its message history with
``agent_common._build_trace``: one ordered entry per meaningful part —
``system`` / ``user`` / ``model`` (its text, thinking, tool calls and that
request's token usage) / ``tool_return`` / ``retry``. ``app.query_runs.trace``,
the admin trace viewer and the eval graders all read that shape, so the Agent
SDK runtime must produce the *same* entries from a completely different stream.

This module does that translation and nothing else. It deliberately dispatches
on ``type(msg).__name__`` rather than ``isinstance`` against the SDK's classes,
so it stays importable — and unit-testable — in the environments that do not
install the optional ``agentsdk`` extra (CI installs only ``--extra llm``).
Duck-typed attribute access on the blocks does the rest.
"""

from __future__ import annotations

from typing import Any

from .agent_common import _stringify
from .knowledge import load_pages
from .otlp import emit_span
from .pricing import cost_usd

# Built-in tools whose file arguments can name a knowledge page.
_FILE_TOOLS = ("Read", "Grep", "Glob")

# ``knowledge.load_pages()`` skips these, so neither the champion's
# ``knowledge_pages_used`` nor its read budget has ever counted them. Reading the
# index is navigation — charging it a knowledge slot would cost the model a
# real page and would misreport which pages produced an answer.
_NOT_PAGES = frozenset({"INDEX.md", "README.md"})


def _knowledge_names_by_rel_path() -> dict[str, str]:
    """``{'domain/nsw-rent.md': 'nsw-rent'}`` — workspace path → playbook name.

    The workspace copies the knowledge tree verbatim under ``knowledge/``, so a
    file the model Reads maps back onto the page name ``knowledge.py`` uses
    (and that ``report["knowledge_pages_used"]`` records on the champion path).
    """
    return {p.rel_path: p.name for p in load_pages()}


def knowledge_page_from_path(path: str) -> str | None:
    """The playbook page name a workspace file path refers to, if any."""
    if not path:
        return None
    normalized = path.replace("\\", "/")
    marker = "knowledge/"
    idx = normalized.rfind(marker)
    if idx == -1:
        return None
    rel = normalized[idx + len(marker) :].lstrip("/")
    if not rel.endswith(".md"):
        return None  # a directory-scoped Grep/Glob names no single page
    if rel.rsplit("/", 1)[-1] in _NOT_PAGES:
        return None  # the index/readme are navigation, not playbook pages
    # An unknown rel_path is never a page: the workspace copies the very tree
    # ``load_pages()`` reads, so anything not in that map is a glob (a Grep for
    # "knowledge/*.md"), a stray file, or a path outside the tree — none of
    # which should spend a knowledge-read slot or be reported as a source.
    return _knowledge_names_by_rel_path().get(rel)


def _block_kind(block: Any) -> str:
    """text | thinking | tool_use | tool_result | other, by duck typing."""
    if hasattr(block, "thinking"):
        return "thinking"
    if hasattr(block, "tool_use_id"):
        return "tool_result"
    if hasattr(block, "name") and hasattr(block, "input"):
        return "tool_use"
    if hasattr(block, "text"):
        return "text"
    return "other"


def _usage_fields(usage: dict[str, Any] | None) -> dict[str, int | None]:
    """The SDK's raw usage dict → the four token fields the app's trace carries.

    Convention conversion, not just renaming: Anthropic's ``input_tokens`` counts
    only the UNCACHED prefix, while every consumer of this app's trace (the ops
    deck's cost tile, ``pricing.cost_usd``, the champion's DeepSeek numbers)
    treats ``input_tokens`` as the TOTAL input with cache read/write as subsets
    of it. Summing them here is what keeps a champion-vs-challenger token
    comparison an apples-to-apples one.
    """
    if not isinstance(usage, dict):
        return {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
        }
    return _normalize(
        uncached_input=usage.get("input_tokens"),
        output=usage.get("output_tokens"),
        cache_read=usage.get("cache_read_input_tokens"),
        cache_write=usage.get("cache_creation_input_tokens"),
    )


def _normalize(
    *,
    uncached_input: Any,
    output: Any,
    cache_read: Any,
    cache_write: Any,
) -> dict[str, int | None]:
    read = int(cache_read or 0)
    write = int(cache_write or 0)
    inp = int(uncached_input or 0) + read + write
    out = int(output or 0)
    total = inp + out
    return {
        "input_tokens": inp or None,
        "output_tokens": out or None,
        "total_tokens": total or None,
        "cache_read_tokens": read or None,
        "cache_write_tokens": write or None,
    }


class SdkTrace:
    """Accumulates one Agent SDK run into the app's flat trace + usage totals."""

    def __init__(self, *, system_prompt: str, question: str, otel_parent: Any = None) -> None:
        # s49 M0: the run span's OTel context, so every translated step can be
        # recorded as a child span under it. ``None`` (the default, and what
        # ``otlp.current_context()`` returns with tracing off) keeps this class
        # a pure translator — unit tests construct it without any OTel at all.
        self.otel_parent = otel_parent
        self.entries: list[dict[str, Any]] = [
            {"kind": "system", "content": system_prompt},
            {"kind": "user", "content": question},
        ]
        self.knowledge_pages: list[str] = []
        self.result: Any | None = None
        self.final_text: str = ""
        self.num_turns: int = 0
        # Assistant messages seen so far — the ``turn`` attribute on the
        # per-turn span. Distinct from ``num_turns``, which the SDK's own
        # ResultMessage reports at the end of the run.
        self.model_turns: int = 0
        self.session_id: str | None = None
        self.total_tokens: int = 0
        # tool_use_id → tool name, so a tool_result entry can name its tool the
        # way the champion's tool-return parts do.
        self._tool_names: dict[str, str] = {}
        # tool_use_id → knowledge page name requested, held here until the
        # matching tool_result confirms the hook did not deny it — see
        # ``_note_knowledge``.
        self._pending_knowledge: dict[str, str] = {}

    # -- stream consumption ------------------------------------------------
    def consume(self, msg: Any) -> None:
        """Fold one SDK message into the trace. Unknown message types are ignored."""
        name = type(msg).__name__
        if name == "AssistantMessage":
            self._consume_assistant(msg)
        elif name == "UserMessage":
            self._consume_user(msg)
        elif name == "ResultMessage":
            self._consume_result(msg)
        # SystemMessage (init metadata) and StreamEvent (partial deltas) carry
        # nothing the champion trace has an entry for.

    def _consume_assistant(self, msg: Any) -> None:
        texts: list[str] = []
        thinking: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        blocks: list[Any] = list(getattr(msg, "content", None) or [])
        for block in blocks:
            kind = _block_kind(block)
            if kind == "text":
                text_value = str(getattr(block, "text", None) or "")
                if text_value:
                    texts.append(text_value)
            elif kind == "thinking":
                think_value = str(getattr(block, "thinking", None) or "")
                if think_value:
                    thinking.append(think_value)
            elif kind == "tool_use":
                tool_id = getattr(block, "id", None)
                tool_name = str(getattr(block, "name", None) or "")
                tool_input = getattr(block, "input", None)
                if tool_id:
                    self._tool_names[str(tool_id)] = tool_name
                tool_calls.append(
                    {
                        "name": tool_name,
                        "args": _stringify(tool_input),
                        "tool_call_id": tool_id,
                    }
                )
                self._note_knowledge(tool_id, tool_name, tool_input)
        text = "\n".join(texts)
        if text.strip():
            self.final_text = text.strip()
        usage_fields = _usage_fields(getattr(msg, "usage", None))
        self.total_tokens += usage_fields.get("total_tokens") or 0
        self.model_turns += 1
        # s49 M0: one child span per model turn, carrying exactly what the flat
        # trace entry carries. A point-in-time span rather than a timed one —
        # the SDK hands the assistant message over only once the turn is
        # already finished, so this process never sees its start.
        emit_span(
            "model.turn",
            parent=self.otel_parent,
            turn=self.model_turns,
            input_tokens=usage_fields.get("input_tokens"),
            output_tokens=usage_fields.get("output_tokens"),
            cache_read=usage_fields.get("cache_read_tokens"),
            cache_write=usage_fields.get("cache_write_tokens"),
            stop_reason=getattr(msg, "stop_reason", None),
            tool_calls=",".join(str(c["name"]) for c in tool_calls) or None,
            model=getattr(msg, "model", None),
        )
        self.entries.append(
            {
                "kind": "model",
                "content": text,
                "thinking": "\n".join(thinking) or None,
                "tool_calls": tool_calls,
                "model_name": getattr(msg, "model", None),
                **usage_fields,
            }
        )

    def _consume_user(self, msg: Any) -> None:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            return  # the initial prompt echo; already recorded as the user entry
        for block in content:
            if _block_kind(block) != "tool_result":
                continue
            tool_use_id = getattr(block, "tool_use_id", None)
            is_error = bool(getattr(block, "is_error", False))
            page = self._pending_knowledge.pop(str(tool_use_id), None)
            if page and not is_error and page not in self.knowledge_pages:
                self.knowledge_pages.append(page)
            self.entries.append(
                {
                    "kind": "tool_return",
                    "name": self._tool_names.get(str(tool_use_id), ""),
                    "tool_call_id": tool_use_id,
                    "content": _stringify(getattr(block, "content", "")),
                    **({"error": True} if is_error else {}),
                }
            )

    def _consume_result(self, msg: Any) -> None:
        self.result = msg
        self.num_turns = int(getattr(msg, "num_turns", 0) or 0)
        self.session_id = getattr(msg, "session_id", None)
        text = getattr(msg, "result", None)
        if isinstance(text, str) and text.strip():
            self.final_text = text.strip()

    def _note_knowledge(self, tool_use_id: Any, tool_name: str, tool_input: Any) -> None:
        """Queue a knowledge page a tool_use block asked for.

        Not recorded as used yet: the PreToolUse hook (``sdk_agent.py``) can
        still deny this call for being over the knowledge-read quota, in which
        case the model never saw the page. The page only moves into
        ``knowledge_pages`` once the matching tool_result comes back without
        ``is_error`` — see ``_consume_user``.
        """
        if tool_name not in _FILE_TOOLS or not isinstance(tool_input, dict) or not tool_use_id:
            return
        for key in ("file_path", "path", "notebook_path", "pattern"):
            page = knowledge_page_from_path(str(tool_input.get(key) or ""))
            if page:
                self._pending_knowledge[str(tool_use_id)] = page
                return

    # -- totals ------------------------------------------------------------
    def usage_totals(self, model_name: str) -> dict[str, Any]:
        """Token totals + priced cost, in the exact shape ``_usage_totals`` returns.

        The ResultMessage is authoritative when present: ``model_usage`` is the
        CLI's own per-model aggregate (including the cache split that makes the
        cost tile honest) and ``total_cost_usd`` is the price it actually
        charged, which beats re-pricing from a local table. Summing the per-turn
        model entries is the fallback for a run that never reached a result.
        """
        totals = self._model_usage_totals()
        if totals is None:
            totals = self._trace_totals()
        cost = getattr(self.result, "total_cost_usd", None) if self.result else None
        if cost is None:
            cost = self._model_usage_cost()
        if cost is None:
            cost = cost_usd(model_id=model_name, **totals)
        return {**totals, "cost_usd": cost}

    def _trace_totals(self) -> dict[str, int | None]:
        model_steps = [s for s in self.entries if s.get("kind") == "model"]

        def total(key: str) -> int | None:
            return sum(s.get(key) or 0 for s in model_steps) or None

        return {
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "cache_read_tokens": total("cache_read_tokens"),
            "cache_write_tokens": total("cache_write_tokens"),
        }

    def _model_usage(self) -> list[dict[str, Any]]:
        raw = getattr(self.result, "model_usage", None) if self.result else None
        if not isinstance(raw, dict):
            return []
        return [v for v in raw.values() if isinstance(v, dict)]

    def _model_usage_totals(self) -> dict[str, int | None] | None:
        entries = self._model_usage()
        if not entries:
            return None

        def total(key: str) -> int:
            return sum(int(e.get(key) or 0) for e in entries)

        fields = _normalize(
            uncached_input=total("inputTokens"),
            output=total("outputTokens"),
            cache_read=total("cacheReadInputTokens"),
            cache_write=total("cacheCreationInputTokens"),
        )
        del fields["total_tokens"]  # a run total is summed by the caller, not stored
        return fields

    def _model_usage_cost(self) -> float | None:
        total = 0.0
        seen = False
        for entry in self._model_usage():
            raw = entry.get("costUSD")
            if raw is None:
                continue
            seen = True
            total += float(raw)
        return total if seen else None
