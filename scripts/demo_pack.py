#!/usr/bin/env python3
"""Export recorded agent runs into the demo replay pack (s38 P1).

Demo-mode deployments answer chat by replaying runs recorded in dev. This tool
is the recorder's other half: it pulls a finished run (full trace + report
pages + SQL) out of ``app.query_runs``/``app.messages`` and writes it to
``services/backend-api/app/demo_pack/<slug>.json`` — inside the backend's build
context, so the pack ships in the image with no Dockerfile change and loads
into memory at boot (chat replay stays instant while Aurora wakes).

The repo is the source of truth, same doctrine as the eval pack: pack files are
reviewed in PRs, and re-recording is just running the questions again in dev
and re-exporting.

Usage (from the repo root; DB via `docker compose exec db`, like eval_pack.py):
    uv run python scripts/demo_pack.py list                # recent candidate runs
    uv run python scripts/demo_pack.py export --runs <id>[,<id>...]
    uv run python scripts/demo_pack.py export --latest 5   # newest successful LLM runs
    uv run python scripts/demo_pack.py show                # what's in the pack now
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parent.parent
PACK_DIR = REPO_ROOT / "services" / "backend-api" / "app" / "demo_pack"

# Engines that are real recorded answers. The stub and editor runs are not
# showcase material; demo_replay runs must never be re-exported (an echo).
_RECORDABLE = ("deepseek", "anthropic", "openai")


def _psql(query: str, service: str = "db") -> str:
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", service, "psql", "-U", "postgres",
         "-d", "dataqa", "-tA", "-f", "-"],
        input=query, capture_output=True, text=True, cwd=REPO_ROOT,
    )
    if proc.returncode != 0:
        sys.exit(f"psql failed: {proc.stderr.strip()}")
    return proc.stdout


def _slugify(question: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")
    return slug[:60].rstrip("-") or "question"


def _fetch_runs(where: str) -> list[dict[str, Any]]:
    engines = ", ".join(f"'{e}'" for e in _RECORDABLE)
    query = f"""
        SELECT json_build_object(
            'run_id', qr.id,
            'question', qr.question,
            'answer', m.content,
            'sql', qr.sql_text,
            'engine', qr.engine,
            'row_count', qr.row_count,
            'latency_ms', qr.latency_ms,
            'steps', coalesce(qr.trace, '[]'::jsonb),
            'report', m.report,
            'recorded_at', qr.created_at
        )
        FROM app.query_runs qr
        JOIN app.messages m ON m.id = qr.message_id
        WHERE qr.status = 'success' AND qr.engine IN ({engines})
          AND m.report IS NOT NULL
          {where}
        ORDER BY qr.created_at DESC;
    """
    return [json.loads(line) for line in _psql(query).splitlines() if line.strip()]


def cmd_list(_args: argparse.Namespace) -> None:
    engines = ", ".join(f"'{e}'" for e in _RECORDABLE)
    out = _psql(
        f"SELECT qr.id || ' | ' || qr.engine || ' | rows=' || qr.row_count "
        f"|| ' | ' || to_char(qr.created_at, 'MM-DD HH24:MI') || ' | ' || left(qr.question, 70) "
        f"FROM app.query_runs qr JOIN app.messages m ON m.id = qr.message_id "
        f"WHERE qr.status = 'success' AND qr.engine IN ({engines}) AND m.report IS NOT NULL "
        f"ORDER BY qr.created_at DESC LIMIT 30;"
    )
    print(out or "no recordable runs found — ask some questions in dev first")


def cmd_export(args: argparse.Namespace) -> None:
    if args.runs:
        # query_runs.id is a uuid column; validating each token as one before
        # interpolating rules out SQL injection via a crafted --runs value.
        try:
            run_ids = [str(UUID(r.strip())) for r in args.runs.split(",")]
        except ValueError as exc:
            sys.exit(f"--runs must be comma-separated UUIDs: {exc}")
        ids = ",".join(f"'{r}'" for r in run_ids)
        rows = _fetch_runs(f"AND qr.id IN ({ids})")
    elif args.latest:
        rows = _fetch_runs("")[: args.latest]
    else:
        sys.exit("pass --runs <id,...> or --latest N")
    if not rows:
        sys.exit("no matching recordable runs (status=success, LLM engine, report present)")

    PACK_DIR.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for row in rows:
        slug = _slugify(row["question"])
        if slug in seen:
            continue  # same question re-asked: keep the newest (rows are DESC)
        seen.add(slug)
        report = row.get("report") or {}
        entry = {
            "id": slug,
            "question": row["question"],
            "answer": row["answer"],
            "sql": row["sql"],
            "engine": row["engine"],
            "row_count": row["row_count"],
            "latency_ms": row["latency_ms"],
            "steps": row["steps"],
            "report": report,
            "pages": report.get("pages") or [],
            "recorded_at": row["recorded_at"],
            "run_id": row["run_id"],
        }
        path = PACK_DIR / f"{slug}.json"
        path.write_text(json.dumps(entry, indent=1) + "\n")
        print(f"exported {path.relative_to(REPO_ROOT)}  ({len(entry['pages'])} pages)")


def cmd_show(_args: argparse.Namespace) -> None:
    files = sorted(PACK_DIR.glob("*.json"))
    if not files:
        print("pack is empty")
        return
    for f in files:
        data = json.loads(f.read_text())
        print(f"{f.name}: {data['question'][:70]}  ({len(data.get('pages') or [])} pages)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    exp = sub.add_parser("export")
    exp.add_argument("--runs", help="comma-separated query_runs ids")
    exp.add_argument("--latest", type=int, help="export the newest N recordable runs")
    exp.set_defaults(func=cmd_export)
    sub.add_parser("show").set_defaults(func=cmd_show)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
