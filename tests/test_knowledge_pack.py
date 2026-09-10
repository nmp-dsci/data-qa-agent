"""scripts/knowledge_pack.py — the export/import round trip (s49 M4, D3).

Two layers of test, mirroring test_eval_pack.py's own split:

* the pure frontmatter-splitting helper (no DB, no docker) — always runs;
* export/import against the live compose Postgres — self-skips exactly like
  test_eval_pack.py's SQL checks when the stack is down, so this stays green
  on a laptop with no docker running.

The round-trip test cleans up the row + log entries it writes and restores
the file it touches, so a run never leaves ``git status`` dirty.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "knowledge_pack.py"

_spec = importlib.util.spec_from_file_location("knowledge_pack", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
knowledge_pack = importlib.util.module_from_spec(_spec)
sys.modules["knowledge_pack"] = knowledge_pack
_spec.loader.exec_module(knowledge_pack)


def test_split_frontmatter_separates_meta_from_body() -> None:
    text = "---\nname: foo\ndescription: bar\n---\n# Heading\nBody text.\n"
    frontmatter_raw, body = knowledge_pack._split_frontmatter(text)
    assert frontmatter_raw == "---\nname: foo\ndescription: bar\n---\n"
    assert body == "# Heading\nBody text.\n"


def test_split_frontmatter_handles_no_frontmatter() -> None:
    text = "just a body, no frontmatter\n"
    frontmatter_raw, body = knowledge_pack._split_frontmatter(text)
    assert frontmatter_raw == ""
    assert body == text


def test_split_frontmatter_round_trips() -> None:
    original = (
        "---\nname: rent-bedrooms\ndescription: rent by bedroom\n---\n# Body\nline one\nline two\n"
    )
    frontmatter_raw, body = knowledge_pack._split_frontmatter(original)
    assert frontmatter_raw + body == original


def _db_available() -> bool:
    try:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "postgres",
                "-d",
                "dataqa",
                "-tA",
                "-c",
                "select 1",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 - no docker, no db: skip rather than fail
        return False


DB_UP = _db_available()

_TEST_REL_PATH = "domains/_s49_knowledge_pack_test/page.md"


@pytest.mark.skipif(not DB_UP, reason="compose db not reachable")
def test_export_writes_a_db_override_back_to_the_file_and_import_is_idempotent() -> None:
    args = _Args(service="db")
    dest = knowledge_pack.KNOWLEDGE_DIR / _TEST_REL_PATH
    assert not dest.exists(), "test fixture path must not already exist on disk"
    try:
        knowledge_pack._psql(
            "INSERT INTO app.knowledge_pages (path, name, body, version, author, updated_at) "
            f"VALUES ({knowledge_pack._sql_literal(_TEST_REL_PATH)}, 'test-page', "
            f"{knowledge_pack._sql_literal('Exported body.')}, 1, 'pytest', now()) "
            "ON CONFLICT (path) DO UPDATE SET body = EXCLUDED.body",
            "db",
        )

        knowledge_pack.cmd_export(args)

        assert dest.is_file()
        content = dest.read_text(encoding="utf-8")
        assert "Exported body." in content
        assert content.startswith("---\n")

        # import is idempotent: a row that already exists in the DB must not
        # be touched by a subsequent import of the (now-exported) file.
        knowledge_pack.cmd_import(args)
        raw = knowledge_pack._psql(
            f"SELECT body, version FROM app.knowledge_pages WHERE path = "
            f"{knowledge_pack._sql_literal(_TEST_REL_PATH)}",
            "db",
        )
        assert "Exported body." in raw
        assert raw.strip().endswith("|1")
    finally:
        knowledge_pack._psql(
            "DELETE FROM app.knowledge_pages_log WHERE path = "
            f"{knowledge_pack._sql_literal(_TEST_REL_PATH)}; "
            "DELETE FROM app.knowledge_pages WHERE path = "
            f"{knowledge_pack._sql_literal(_TEST_REL_PATH)}",
            "db",
        )
        if dest.exists():
            dest.unlink()
            for parent in [dest.parent]:
                try:
                    parent.rmdir()
                except OSError:
                    pass


class _Args:
    def __init__(self, *, service: str) -> None:
        self.service = service
        self.dataset = None
