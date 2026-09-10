"""Tests for the Agent SDK per-run filesystem workspace (agent_sdk M2).

Offline by design: schema.list_marts()/describe_table() are pure-Python
(dbt-manifest or curated-catalog fallback) and never touch the DB, but we still
force the curated-fallback branch by clearing DBT_MANIFEST so these tests don't
depend on whatever the host environment happens to have set.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from agent import knowledge as knowledge_mod
from agent.schema import USER_VISIBLE_SCHEMAS, describe_table, get_catalog
from agent.workspace import (
    build_workspace,
    cleanup_workspace,
    workspace,
    workspace_manifest,
)


@pytest.fixture(autouse=True)
def _force_curated_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the schema functions to the offline curated-catalog fallback path."""
    monkeypatch.delenv("DBT_MANIFEST", raising=False)


def _user_tables() -> list[dict[str, str]]:
    return [t for t in get_catalog(role="user") if t["schema"] in USER_VISIBLE_SCHEMAS]


def test_build_workspace_creates_expected_layout(tmp_path: Path) -> None:
    ws = build_workspace(
        "run-abc123",
        "What is the trend in rent?",
        include_insights=True,
        base_dir=tmp_path,
    )

    assert ws == tmp_path / "runs" / "run-abc123"
    assert ws.is_dir()

    claude_md = ws / "CLAUDE.md"
    marts_md = ws / "marts.md"
    assert claude_md.is_file() and claude_md.read_text().strip()
    assert marts_md.is_file() and marts_md.read_text().strip()

    schema_dir = ws / "schema"
    assert schema_dir.is_dir()
    schema_files = sorted(p.name for p in schema_dir.glob("*.md"))
    assert schema_files, "expected at least one schema/*.md file"
    for path in schema_dir.glob("*.md"):
        assert path.read_text().strip()

    knowledge_dir = ws / "knowledge"
    assert knowledge_dir.is_dir()
    assert list(knowledge_dir.rglob("*.md")), "expected the knowledge tree to be copied"

    frames_dir = ws / "frames"
    assert frames_dir.is_dir()
    readme = frames_dir / "README.md"
    assert readme.is_file() and readme.read_text().strip()


def test_marts_md_contains_every_mart_name(tmp_path: Path) -> None:
    ws = build_workspace("run-marts", "any question", include_insights=True, base_dir=tmp_path)
    marts_md = (ws / "marts.md").read_text()
    for table in _user_tables():
        assert table["table"] in marts_md


def test_schema_files_match_describe_table(tmp_path: Path) -> None:
    ws = build_workspace("run-schema", "any question", include_insights=True, base_dir=tmp_path)
    tables = _user_tables()
    assert tables, "curated catalog should expose at least one user-visible table"
    seen_files = set()
    for table in tables:
        relation = f"{table['schema']}.{table['table']}"
        filename = f"{table['schema']}_{table['table']}.md"
        seen_files.add(filename)
        content = (ws / "schema" / filename).read_text()
        assert content == describe_table(relation)
    on_disk = {p.name for p in (ws / "schema").glob("*.md")}
    assert on_disk == seen_files


def test_knowledge_copy_matches_source_tree(tmp_path: Path) -> None:
    ws = build_workspace("run-knowledge", "any question", include_insights=True, base_dir=tmp_path)
    source_dir = knowledge_mod._knowledge_dir()  # noqa: SLF001 — same accessor workspace.py uses
    copy_hash = knowledge_mod._version_for(str(ws / "knowledge"))  # noqa: SLF001
    source_hash = knowledge_mod._version_for(str(source_dir))  # noqa: SLF001
    assert copy_hash == source_hash
    # s49 (D3): knowledge_version() folds a curator DB-override snapshot into
    # this file-tree hash (see test_knowledge.py for that composition on its
    # own), so it no longer equals the plain tree hash even with no overrides
    # in play — verify the exact composition instead of bare equality.
    expected = hashlib.sha256(
        f"{copy_hash}:{knowledge_mod._overrides_snapshot_hash()}".encode()  # noqa: SLF001
    ).hexdigest()[:12]
    assert knowledge_mod.knowledge_version() == expected


@pytest.mark.parametrize("include_insights", [True, False])
def test_claude_md_has_no_leftover_template_slots(tmp_path: Path, include_insights: bool) -> None:
    ws = build_workspace(
        f"run-slots-{include_insights}",
        "any question",
        include_insights=include_insights,
        memories_block="- prefers charts in AUD",
        base_dir=tmp_path,
    )
    content = (ws / "CLAUDE.md").read_text()
    assert "{{" not in content
    assert "}}" not in content
    assert "prefers charts in AUD" in content


def test_claude_md_generates_the_skills_block_from_the_registry(tmp_path: Path) -> None:
    """The {{SKILLS}} slot must render every registered skill's signature and
    first docstring line (s49 M1) — no hand-maintained list to drift."""
    from agent import skills as skills_mod

    ws = build_workspace(
        "run-skills-block", "any question", include_insights=True, base_dir=tmp_path
    )
    content = (ws / "CLAUDE.md").read_text()
    for _module, name, signature, _doc in skills_mod.registered():
        assert signature in content, f"{name}'s signature missing from CLAUDE.md"
    # The mechanics footer (hand-written, never generated) is still present.
    assert "skill_gap(need, why=" in content
    assert "note_inline_math()" in content


def test_claude_md_omits_memories_section_when_empty(tmp_path: Path) -> None:
    ws = build_workspace(
        "run-no-memories", "any question", include_insights=True, base_dir=tmp_path
    )
    content = (ws / "CLAUDE.md").read_text()
    assert "Known preferences" not in content


def test_workspace_manifest_has_stable_components(tmp_path: Path) -> None:
    ws = build_workspace("run-manifest", "any question", include_insights=True, base_dir=tmp_path)
    manifest = workspace_manifest(ws)
    for key in ("claude_md", "marts", "schema", "knowledge", "combined"):
        assert manifest[key], f"{key} must be populated"
    assert manifest["knowledge"] == knowledge_mod.knowledge_version()
    # Rebuilding an identical workspace must reproduce every hash exactly.
    ws2 = build_workspace(
        "run-manifest-2", "any question", include_insights=True, base_dir=tmp_path
    )
    manifest2 = workspace_manifest(ws2)
    assert manifest == manifest2


def test_cleanup_workspace_removes_the_directory(tmp_path: Path) -> None:
    ws = build_workspace("run-cleanup", "any question", include_insights=True, base_dir=tmp_path)
    assert ws.exists()
    cleanup_workspace(ws)
    assert not ws.exists()
    # Idempotent — cleaning up twice must not raise.
    cleanup_workspace(ws)


@pytest.mark.parametrize("bad_run_id", ["", "../escape", "a/b", "a\\b", "..", "run id with spaces"])
def test_bad_run_id_raises(tmp_path: Path, bad_run_id: str) -> None:
    with pytest.raises(ValueError):
        build_workspace(bad_run_id, "any question", include_insights=True, base_dir=tmp_path)


def test_empty_question_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_workspace("run-empty-question", "   ", include_insights=True, base_dir=tmp_path)


def test_async_context_manager_builds_and_cleans_up(tmp_path: Path) -> None:
    async def _run() -> Path:
        async with workspace(
            "run-ctx", "any question", include_insights=False, base_dir=tmp_path
        ) as ws:
            assert ws.is_dir()
            assert (ws / "CLAUDE.md").is_file()
            captured = ws
        assert not captured.exists()
        return captured

    asyncio.run(_run())


def test_knowledge_copy_prefers_a_db_override_body(tmp_path: Path) -> None:
    """s49 (D3): a curator's ``app.knowledge_pages`` override wins over the
    file body in the copied ``knowledge/`` tree — build_workspace() itself
    stays DB-free (it only reads whatever is already in knowledge_mod's
    module-global override cache)."""
    pages = knowledge_mod.load_pages()
    assert pages, "fixture knowledge tree should have at least one page"
    target = pages[0]
    original_overrides = knowledge_mod._OVERRIDES  # noqa: SLF001
    original_loaded_at = knowledge_mod._overrides_loaded_at  # noqa: SLF001
    try:
        knowledge_mod._OVERRIDES = {  # noqa: SLF001
            target.rel_path: {
                "body": "CURATOR OVERRIDE BODY — should win in the copy.",
                "version": 7,
                "author": "curator1",
                "updated_at": "",
            }
        }
        knowledge_mod._overrides_loaded_at = 0.0  # noqa: SLF001
        knowledge_mod._load_pages_cached.cache_clear()  # noqa: SLF001

        ws = build_workspace(
            "run-knowledge-override", "any question", include_insights=True, base_dir=tmp_path
        )
        copied = (ws / "knowledge" / target.rel_path).read_text(encoding="utf-8")
        assert "CURATOR OVERRIDE BODY — should win in the copy." in copied
    finally:
        knowledge_mod._OVERRIDES = original_overrides  # noqa: SLF001
        knowledge_mod._overrides_loaded_at = original_loaded_at  # noqa: SLF001
        knowledge_mod._load_pages_cached.cache_clear()  # noqa: SLF001


def test_workspace_context_manager_refreshes_knowledge_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The async ``workspace()`` wrapper awaits ``load_overrides()`` before
    the sync build, so a live curator edit is picked up without the caller
    having to remember to refresh it itself."""
    calls = {"n": 0}

    async def _fake_load_overrides(*, ttl: float = 0.0) -> None:
        calls["n"] += 1

    monkeypatch.setattr(knowledge_mod, "load_overrides", _fake_load_overrides)

    async def _run() -> None:
        async with workspace(
            "run-ctx-overrides", "any question", include_insights=False, base_dir=tmp_path
        ):
            pass

    asyncio.run(_run())
    assert calls["n"] == 1
