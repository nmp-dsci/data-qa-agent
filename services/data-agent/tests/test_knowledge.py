"""Knowledge curator DB-override layer (s49 M4, D3).

Offline by design, same shape as ``test_ordinals_snapshot.py``: no live DB.
``load_overrides()`` is the module's one DB-fetch layer; every test sets the
module-global cache directly (what a fresh, successful ``load_overrides()``
call would leave behind) and points ``KNOWLEDGE_DIR`` at a small fixture tree
built in ``tmp_path`` so file content is fully controlled.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import knowledge


def _write_page(root: Path, rel: str, *, name: str, description: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8")


@pytest.fixture()
def knowledge_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "knowledge"
    root.mkdir()
    _write_page(
        root,
        "domains/rent/overview.md",
        name="rent-overview",
        description="rent mart overview",
        body="# Rent overview\nOriginal file body.\n",
    )
    monkeypatch.setenv("KNOWLEDGE_DIR", str(root))
    # Every test starts with a clean override cache and cold lru_caches so
    # nothing leaks between tests via module globals.
    knowledge._OVERRIDES = None
    knowledge._overrides_loaded_at = 0.0
    knowledge._load_pages_cached.cache_clear()
    knowledge._version_for.cache_clear()
    yield root
    knowledge._OVERRIDES = None
    knowledge._overrides_loaded_at = 0.0
    knowledge._load_pages_cached.cache_clear()
    knowledge._version_for.cache_clear()


def _set_overrides(overrides: dict[str, dict[str, object]]) -> None:
    knowledge._OVERRIDES = overrides
    knowledge._overrides_loaded_at = 0.0


def test_file_only_page_has_no_override(knowledge_root: Path) -> None:
    _set_overrides({})
    pages = knowledge.load_pages()
    assert len(pages) == 1
    page = pages[0]
    assert page.source == "file"
    assert page.version == 0
    assert "Original file body." in page.body


def test_db_override_wins_over_file_body(knowledge_root: Path) -> None:
    _set_overrides(
        {
            "domains/rent/overview.md": {
                "body": "Curator-edited body.",
                "version": 3,
                "author": "curator1",
                "updated_at": "2026-09-10T00:00:00+00:00",
            }
        }
    )
    pages = knowledge.load_pages()
    assert len(pages) == 1
    page = pages[0]
    assert page.source == "db"
    assert page.version == 3
    assert page.author == "curator1"
    assert page.body == "Curator-edited body."
    # Frontmatter (name/description) still comes from the file, never the DB.
    assert page.name == "rent-overview"
    assert page.description == "rent mart overview"


def test_get_page_and_list_pages_meta_reflect_override(knowledge_root: Path) -> None:
    _set_overrides(
        {
            "domains/rent/overview.md": {
                "body": "Curator body.",
                "version": 1,
                "author": "curator1",
                "updated_at": "",
            }
        }
    )
    page = knowledge.get_page("domains/rent/overview.md")
    assert page is not None
    assert page.source == "db"
    assert page.body == "Curator body."

    meta = knowledge.list_pages_meta()
    assert len(meta) == 1
    assert meta[0]["path"] == "domains/rent/overview.md"
    assert meta[0]["source"] == "db"
    assert meta[0]["version"] == 1


def test_db_only_page_synthesizes_a_page_entry(knowledge_root: Path) -> None:
    """A page authored purely through the curator UI (never exported to a file)
    still shows up in load_pages()/list_pages_meta()."""
    _set_overrides(
        {
            "domains/rent/new-page.md": {
                "body": "Brand new curator page.",
                "version": 1,
                "author": "curator1",
                "updated_at": "",
            }
        }
    )
    pages = knowledge.load_pages()
    rels = {p.rel_path for p in pages}
    assert "domains/rent/new-page.md" in rels
    assert "domains/rent/overview.md" in rels
    new_page = next(p for p in pages if p.rel_path == "domains/rent/new-page.md")
    assert new_page.source == "db"
    assert new_page.body == "Brand new curator page."


def test_knowledge_version_changes_when_override_body_changes(knowledge_root: Path) -> None:
    _set_overrides({})
    baseline = knowledge.knowledge_version()

    _set_overrides(
        {
            "domains/rent/overview.md": {
                "body": "Curator-edited body.",
                "version": 1,
                "author": "curator1",
                "updated_at": "",
            }
        }
    )
    overridden = knowledge.knowledge_version()
    assert overridden != baseline

    # A second, different edit moves the version again.
    _set_overrides(
        {
            "domains/rent/overview.md": {
                "body": "A different curator edit.",
                "version": 2,
                "author": "curator1",
                "updated_at": "",
            }
        }
    )
    overridden_again = knowledge.knowledge_version()
    assert overridden_again != overridden
    assert overridden_again != baseline


def test_knowledge_version_is_stable_for_the_same_state(knowledge_root: Path) -> None:
    _set_overrides(
        {
            "domains/rent/overview.md": {
                "body": "Curator-edited body.",
                "version": 1,
                "author": "curator1",
                "updated_at": "",
            }
        }
    )
    first = knowledge.knowledge_version()
    second = knowledge.knowledge_version()
    assert first == second


def test_load_overrides_degrades_to_empty_on_db_failure(
    knowledge_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DB outage must never raise — the file tree alone still answers."""
    import asyncio

    class _BoomEngine:
        def connect(self) -> None:  # pragma: no cover - never actually awaited
            raise RuntimeError("db unreachable")

    monkeypatch.setattr("agent.db.engine", _BoomEngine())
    knowledge._OVERRIDES = None
    knowledge._overrides_loaded_at = 0.0

    asyncio.run(knowledge.load_overrides())

    assert knowledge._OVERRIDES == {}
    pages = knowledge.load_pages()
    assert len(pages) == 1
    assert pages[0].source == "file"
