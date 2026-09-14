#!/usr/bin/env python3
"""Move curator knowledge edits between the database and the repo (s49 M4, D3).

D3 locks the direction: the knowledge tree stays a markdown filesystem
(``services/data-agent/knowledge/``), plus a DB curator write path
(``app.knowledge_pages``, migration 0039) that this script exports back to
git — the same "DB is a working surface, the repo is the source of truth"
shape ``scripts/eval_pack.py`` already gives goldens. A curator edit made in
the Architecture tab is live for the agent immediately (data-agent reads the
DB override on a 5s TTL, see ``agent/knowledge.py``), but invisible to any
other clone/CI/prod until someone runs ``export`` and commits the file.

Usage (from the repo root; the DB is reached via `docker compose exec db`,
mirroring eval_pack.py so no extra DB driver is needed at the repo root):
    uv run python scripts/knowledge_pack.py export   # DB  -> services/data-agent/knowledge/*.md
    uv run python scripts/knowledge_pack.py import   # files -> DB (seed only, never overwrites)

Add `--service NAME` if the db compose service isn't named "db".
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "services" / "data-agent" / "knowledge"

_SKIP_FILES = {"INDEX.md", "README.md"}


def _psql(query: str, service: str) -> str:
    """Run one statement in the compose Postgres and return raw stdout.

    Piped over stdin (psql -f -), not -c: a page body can be long enough to
    blow the container exec's argv limit. Mirrors eval_pack.py's ``_psql``.
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
            "-f",
            "-",
        ],
        cwd=REPO_ROOT,
        input=query,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"psql failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Frontmatter — kept in step with agent/knowledge.py's parser (this script
# deliberately doesn't import the agent package: it's a separate service with
# its own venv/container, and a root-level script staying dependency-light
# matches eval_pack.py's own "stdlib + yaml only" discipline).
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_raw, body). frontmatter_raw is "" when there is none."""
    if not text.startswith("---"):
        return "", text
    parts = text.split("\n", 1)
    if len(parts) < 2:
        return "", text
    rest = parts[1]
    end = rest.find("\n---")
    if end == -1:
        return "", text
    front = rest[:end]
    body = rest[end + len("\n---") :].lstrip("\n")
    return f"---\n{front}\n---\n", body


def _iter_files() -> list[Path]:
    if not KNOWLEDGE_DIR.is_dir():
        return []
    return [p for p in sorted(KNOWLEDGE_DIR.rglob("*.md")) if p.name not in _SKIP_FILES]


def _rel(path: Path) -> str:
    return str(path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/")


# ---------------------------------------------------------------------------
# export — DB rows win, written back into the file tree.
# ---------------------------------------------------------------------------


def _fetch_pages(service: str) -> list[dict[str, Any]]:
    raw = _psql(
        "SELECT coalesce(json_agg(json_build_object("
        "'path', path, 'name', name, 'body', body, 'version', version, "
        "'author', author, 'updated_at', updated_at"
        ")), '[]') FROM app.knowledge_pages",
        service,
    )
    result: list[dict[str, Any]] = json.loads(raw or "[]")
    return result


def cmd_export(args: argparse.Namespace) -> None:
    rows = _fetch_pages(args.service)
    if not rows:
        print("no curator overrides in app.knowledge_pages — nothing to export")
        return
    knowledge_root = KNOWLEDGE_DIR.resolve()
    changed: list[str] = []
    refused: list[str] = []
    for row in rows:
        rel = str(row["path"])
        dest = (KNOWLEDGE_DIR / rel).resolve()
        try:
            dest.relative_to(knowledge_root)
        except ValueError:
            refused.append(rel)
            continue
        if dest.exists():
            frontmatter_raw, _old_body = _split_frontmatter(dest.read_text(encoding="utf-8"))
            if not frontmatter_raw:
                frontmatter_raw = f"---\nname: {row['name']}\n---\n"
        else:
            frontmatter_raw = f"---\nname: {row['name']}\n---\n"
        new_content = frontmatter_raw + str(row["body"])
        if dest.exists() and dest.read_text(encoding="utf-8") == new_content:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_content, encoding="utf-8")
        changed.append(str(dest.relative_to(REPO_ROOT)))
    if refused:
        print(f"refused {len(refused)} page(s) whose path resolves outside {KNOWLEDGE_DIR}:")
        for rel in refused:
            print(f"  {rel}")
    if not changed:
        print(
            f"exported {len(rows) - len(refused)} DB page(s) — "
            "file tree already matched, nothing changed"
        )
        return
    print(f"exported {len(rows) - len(refused)} DB page(s), {len(changed)} file(s) changed:")
    for path in changed:
        print(f"  {path}")


# ---------------------------------------------------------------------------
# import — seed DB rows from files, idempotent (only when the DB has no row).
# ---------------------------------------------------------------------------


def cmd_import(args: argparse.Namespace) -> None:
    files = _iter_files()
    if not files:
        sys.exit(f"no knowledge files under {KNOWLEDGE_DIR.relative_to(REPO_ROOT)}")
    statements: list[str] = []
    seeded: list[str] = []
    for path in files:
        rel = _rel(path)
        _frontmatter_raw, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        name = path.stem
        statements.append(
            "INSERT INTO app.knowledge_pages (path, name, body, version, author, updated_at) "
            f"VALUES ({_sql_literal(rel)}, {_sql_literal(name)}, {_sql_literal(body)}, 1, "
            f"{_sql_literal('knowledge-import')}, now()) "
            "ON CONFLICT (path) DO NOTHING"
        )
        seeded.append(rel)
    _psql("\n".join(statements), args.service)
    print(f"seeded (idempotent) {len(seeded)} page(s) from {KNOWLEDGE_DIR.relative_to(REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export", "import"])
    parser.add_argument("--service", default="db", help="compose service name for Postgres")
    args = parser.parse_args()
    if args.command == "export":
        cmd_export(args)
    else:
        cmd_import(args)


if __name__ == "__main__":
    main()
