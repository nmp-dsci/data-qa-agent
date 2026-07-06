#!/usr/bin/env python3
"""Dump one agent run end-to-end for diagnosis.

Every question asked in chat writes an `app.query_runs` row that captures the
question, the full synchronous agent trace (system prompt, every model turn with
its tokens, tool calls + returns), and — via message_id — the delivered report.
This tool pulls one such run and prints it readably, so you can eyeball it or
paste it to Claude and say "here's what happened for this question, diagnose it".

Usage (from the repo root; the DB is reached via `docker compose exec db`):
    uv run python scripts/inspect_run.py                 # the latest agent run
    uv run python scripts/inspect_run.py <run-id>        # by query_runs.id
    uv run python scripts/inspect_run.py --message <id>  # by messages.id
    uv run python scripts/inspect_run.py --match rent    # latest run whose question matches
    uv run python scripts/inspect_run.py --json          # raw JSON (pipe to a file / to Claude)
    uv run python scripts/inspect_run.py <run-id> --json > run.json

Add `--service NAME` if the db compose service isn't named "db".
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def _sql_literal(value: str) -> str:
    """Single-quote and escape a value for inlining into SQL (local dev tool)."""
    return "'" + value.replace("'", "''") + "'"


def _selector(args: argparse.Namespace) -> str:
    if args.message:
        return f"qr.message_id = {_sql_literal(args.message)}"
    if args.run_id:
        return f"qr.id = {_sql_literal(args.run_id)}"
    if args.match:
        return f"qr.question ILIKE {_sql_literal('%' + args.match + '%')}"
    return "qr.source = 'agent'"  # latest agent run


def _fetch(selector: str, service: str) -> dict[str, Any] | None:
    query = f"""
        SELECT to_jsonb(x) FROM (
            SELECT qr.id, qr.created_at, u.username, qr.question, qr.engine,
                   qr.source, qr.channel, qr.row_count, qr.latency_ms, qr.input_tokens,
                   qr.output_tokens, qr.status, qr.error, qr.sql_text,
                   qr.message_id, qr.conversation_id, qr.trace, m.report
            FROM app.query_runs qr
            JOIN app.users u ON u.id = qr.user_id
            LEFT JOIN app.messages m ON m.id = qr.message_id
            WHERE {selector}
            ORDER BY qr.created_at DESC
            LIMIT 1
        ) x;
    """
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            service,
            "psql",
            "-U",
            "postgres",
            "-d",
            "dataqa",
            "-tA",
            "-c",
            query,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"psql failed:\n{proc.stderr.strip()}")
    out = proc.stdout.strip()
    if not out:
        return None
    return json.loads(out)


def _fmt_tokens(n: Any) -> str:
    return f"{n:,}" if isinstance(n, int) else "—"


def _print_human(run: dict[str, Any]) -> None:
    p = print
    p("=" * 88)
    p(f"RUN {run['id']}")
    p(f"  when      : {run['created_at']}")
    p(f"  user      : {run['username']}")
    p(f"  question  : {run['question']}")
    p(f"  engine    : {run['engine']}  |  source: {run['source']}  |  status: {run['status']}")
    cache_read = sum((s.get("cache_read_tokens") or 0) for s in (run.get("trace") or []))
    cache_note = f" (cache_read {_fmt_tokens(cache_read)})" if cache_read else ""
    p(
        f"  rows      : {run['row_count']}  |  latency: {run['latency_ms']} ms"
        f"  |  tokens: {_fmt_tokens(run['input_tokens'])} in{cache_note} / "
        f"{_fmt_tokens(run['output_tokens'])} out"
    )
    if run.get("error"):
        p(f"  error     : {run['error']}")
    p(f"  message_id: {run['message_id']}  (delivered report joins here)")
    p("=" * 88)

    trace = run.get("trace") or []
    p(f"\nTRACE — {len(trace)} steps (exact synchronous order)\n")
    for i, s in enumerate(trace, 1):
        kind = s.get("kind", "?")
        head = f"[{i:>3}] {kind.upper()}"
        if kind == "model":
            head += (
                f"  ({s.get('model_name') or '?'})  "
                f"{_fmt_tokens(s.get('input_tokens'))} in / "
                f"{_fmt_tokens(s.get('output_tokens'))} out"
            )
            if s.get("cache_read_tokens"):
                head += f" · cache_read {_fmt_tokens(s.get('cache_read_tokens'))}"
        elif s.get("name"):
            head += f"  · {s['name']}"
        p(head)
        if s.get("thinking"):
            p(_indent("thinking:\n" + s["thinking"]))
        if s.get("content"):
            p(_indent(s["content"]))
        for call in s.get("tool_calls") or []:
            p(_indent(f"→ calls {call.get('name')}"))
            p(_indent(call.get("args", ""), level=2))
        if s.get("kind") == "decision_log":
            for d in s.get("decisions") or []:
                line = f"{d.get('order', '')}. {d.get('type')}: {d.get('choice', '')}"
                if d.get("status"):
                    line += f" [{d['status']}]"
                if d.get("row_count") is not None:
                    line += f" · {d['row_count']} rows"
                p(_indent(line))
                if d.get("why"):
                    p(_indent("why: " + str(d["why"]), level=2))
                if d.get("sql"):
                    p(_indent("sql: " + str(d["sql"]), level=2))
        # Legacy hand-built step fields.
        for key in ("sql", "error", "fact"):
            if s.get(key):
                p(_indent(f"{key}: {s[key]}"))
        p("")

    report = run.get("report")
    p("-" * 88)
    if not report:
        p("DELIVERED REPORT: (none — offline stub or no report produced)")
        return
    p("DELIVERED REPORT")
    p(_indent("summary: " + str(report.get("summary", ""))))
    for h in report.get("headlines", []):
        p(_indent(f"headline: {h.get('label')} = {h.get('value')}  ({h.get('basis', '')})"))
    for ins in report.get("insights", []):
        p(_indent(f"insight: {ins.get('heading')} — {ins.get('body')}"))
    for q in report.get("queries", []):
        p(_indent(f"{q.get('ref')}: {q.get('purpose', '')} · {q.get('row_count')} rows"))
    p(
        _indent(
            f"main_chart: {'yes' if report.get('main_chart') else 'no'}  |  "
            f"knowledge @ {str(report.get('knowledge_version', ''))[:7]}"
        )
    )


def _indent(text: str, level: int = 1) -> str:
    pad = "      " * level
    return "\n".join(pad + line for line in str(text).splitlines())


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("run_id", nargs="?", help="query_runs.id (or a question substring)")
    ap.add_argument("--message", help="look up by messages.id instead")
    ap.add_argument("--match", help="latest run whose question ILIKE %%text%%")
    ap.add_argument("--json", action="store_true", help="print the raw JSON row")
    ap.add_argument("--service", default="db", help="docker compose db service name (default: db)")
    args = ap.parse_args()

    # A positional that isn't a uuid is treated as a question match, for convenience.
    if args.run_id and not _UUID_RE.match(args.run_id):
        args.match, args.run_id = args.run_id, None

    run = _fetch(_selector(args), args.service)
    if run is None:
        sys.exit("No matching query run found.")
    if args.json:
        print(json.dumps(run, indent=2, default=str))
    else:
        _print_human(run)


if __name__ == "__main__":
    main()
