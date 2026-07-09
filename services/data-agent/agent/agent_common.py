"""Shared agent machinery — provider env, trace flattening, lookup SQL.

Helpers used by the sandbox agent path (``sandbox_agent``) that are independent
of any one agent architecture. They lived in the now-removed orchestrator
(``llm_agent``); relocated here so the sandbox path owns no orchestrator code.

Importing this module (via ``sandbox_agent``) instruments pydantic-ai + httpx for
Logfire, so it must be imported *after* ``logfire.configure()`` has run (main.py
configures Logfire before importing anything that reaches here).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import logfire

try:
    from pydantic_ai import Agent, RunContext, capture_run_messages  # noqa: F401
    from pydantic_ai.usage import UsageLimits  # noqa: F401

    logfire.instrument_pydantic_ai()
    logfire.instrument_httpx(capture_all=True)
    _PYDANTIC_AI_AVAILABLE = True
except ImportError:
    _PYDANTIC_AI_AVAILABLE = False

# Which env var carries each provider's key (pydantic-ai reads it from the env).
_ENV_VAR = {"deepseek": "DEEPSEEK_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def _stringify(value: Any) -> str:
    """JSON-safe string for a trace payload (tool args/returns, prompts)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _tool_args(part: Any) -> Any:
    """A ToolCallPart's args, as a dict when the model sent JSON text."""
    args = getattr(part, "args", None)
    if isinstance(args, str):
        try:
            return json.loads(args)
        except (ValueError, TypeError):
            return args
    return args


def _build_trace(messages: list[Any]) -> list[dict[str, Any]]:
    """Flatten the pydantic-ai message history into an ordered, JSON-safe trace.

    One step per meaningful part, in exact chronological order — the system
    prompt, the user question, each model turn (its text/thinking, the tool calls
    it made, and that request's token usage), each tool return, and any retry
    prompts. This is the exact synchronous transcript the admin/chat trace shows,
    so nothing the agent did is hidden.
    """
    trace: list[dict[str, Any]] = []
    for msg in messages:
        parts = getattr(msg, "parts", None) or []
        if getattr(msg, "kind", None) == "response":
            texts: list[str] = []
            thinking: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for part in parts:
                pk = getattr(part, "part_kind", "")
                if pk == "text" and getattr(part, "content", None):
                    texts.append(str(part.content))
                elif pk == "thinking" and getattr(part, "content", None):
                    thinking.append(str(part.content))
                elif pk == "tool-call":
                    tool_calls.append(
                        {
                            "name": getattr(part, "tool_name", ""),
                            "args": _stringify(_tool_args(part)),
                            "tool_call_id": getattr(part, "tool_call_id", None),
                        }
                    )
            usage = getattr(msg, "usage", None)
            trace.append(
                {
                    "kind": "model",
                    "content": "\n".join(texts),
                    "thinking": "\n".join(thinking) or None,
                    "tool_calls": tool_calls,
                    "model_name": getattr(msg, "model_name", None),
                    "input_tokens": getattr(usage, "input_tokens", None) if usage else None,
                    "output_tokens": getattr(usage, "output_tokens", None) if usage else None,
                    "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
                    # Prompt-cache hits: providers (DeepSeek, Anthropic) bill a
                    # cached prefix ~10x cheaper, so input_tokens overstates real
                    # cost. cache_read_tokens is the already-billed-cheap subset —
                    # recorded so a run reports EFFECTIVE, not nominal, token cost.
                    "cache_read_tokens": getattr(usage, "cache_read_tokens", None)
                    if usage
                    else None,
                    "cache_write_tokens": getattr(usage, "cache_write_tokens", None)
                    if usage
                    else None,
                }
            )
        else:  # request: system prompt, user question, tool returns, retries
            for part in parts:
                pk = getattr(part, "part_kind", "")
                if pk == "system-prompt":
                    trace.append({"kind": "system", "content": str(part.content)})
                elif pk == "user-prompt":
                    trace.append({"kind": "user", "content": _stringify(part.content)})
                elif pk == "tool-return":
                    trace.append(
                        {
                            "kind": "tool_return",
                            "name": getattr(part, "tool_name", ""),
                            "tool_call_id": getattr(part, "tool_call_id", None),
                            "content": _stringify(getattr(part, "content", "")),
                        }
                    )
                elif pk == "retry-prompt":
                    trace.append(
                        {
                            "kind": "retry",
                            "name": getattr(part, "tool_name", None),
                            "content": _stringify(getattr(part, "content", "")),
                        }
                    )
    return trace


@lru_cache(maxsize=1)
def _catalog_columns() -> dict[str, set[str]]:
    """{'marts.property_sales': {'suburb', ...}} for lookup_values validation."""
    from .schema import get_catalog

    out: dict[str, set[str]] = {}
    for t in get_catalog(role="user"):
        rel = f"{t['schema']}.{t['table']}"
        out[rel] = {c["name"] for c in t.get("columns", [])}
    return out


def _lookup_values_sql(table: str, column: str, pattern: str) -> str | None:
    """Build a bounded DISTINCT lookup, or None if table/column aren't in the catalog.

    Table and column are validated against the queryable catalog (they're
    interpolated, not bound, so they must be allowlisted identifiers); the
    user-supplied pattern is a value, so it's single-quote-escaped and matched
    with ILIKE. run_select still enforces SELECT-only, RLS, and the timeout.
    """
    cols = _catalog_columns().get(table)
    if cols is None or column not in cols:
        return None

    def _ilike(raw: str) -> str:
        needle = raw.strip().replace("'", "''")
        if "%" not in needle and "_" not in needle:
            needle = f"%{needle}%"
        return f"{column} ILIKE '{needle}'"

    # `a|b` resolves several values in ONE call (models naturally write it) —
    # each alternative is escaped separately and OR-ed.
    alternatives = [p for p in pattern.split("|") if p.strip()] or [pattern]
    where = " OR ".join(_ilike(p) for p in alternatives)
    return (
        f"SELECT DISTINCT {column} FROM {table} "
        f"WHERE {where} ORDER BY {column} LIMIT 50"
    )
