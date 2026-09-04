"""The build fingerprint's contract (s24 M1).

The eval loop's central claim is "this improvement came from one lever". That
claim is only as good as the fingerprint: it has to change when a behaviour
surface changes, stay put when nothing does, and move *independently* per
surface so a comparison can name which lever moved.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent import version
from agent.config import settings


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """The hashes are lru_cached, so each test must start from cold."""
    version.prompt_hash.cache_clear()
    version.skills_hash.cache_clear()
    version._sdk_workspace_hashes.cache_clear()


@pytest.fixture(autouse=True)
def _force_curated_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_sdk_fingerprint builds a reference workspace under the hood
    (workspace.build_workspace) — pin it to the offline curated-catalog
    fallback so this doesn't depend on whatever the host has set."""
    monkeypatch.delenv("DBT_MANIFEST", raising=False)


def test_fingerprint_has_every_component() -> None:
    fp = version.build_fingerprint()
    for key in (
        "fingerprint",
        "provider",
        "model_id",
        "prompt_hash",
        "skills_hash",
        "knowledge_version",
        "label",
    ):
        assert fp[key], f"{key} must be populated"
    assert fp["fingerprint"].startswith("av-")


def test_fingerprint_is_stable_across_calls() -> None:
    """Nothing changed, so the build is the same build — otherwise every run
    would look like a new agent and no baseline could ever be compared."""
    assert version.build_fingerprint() == version.build_fingerprint()


def test_prompt_hash_tracks_prompt_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Editing a prompt module must move prompt_hash — and only prompt_hash."""
    fake = tmp_path / "agent"
    fake.mkdir()
    (fake / "sandbox_agent.py").write_text("SYSTEM = 'v1'")
    (fake / "skills").mkdir()
    (fake / "skills" / "analysis.py").write_text("def mean(): ...")
    monkeypatch.setattr(version, "_AGENT_DIR", fake)
    monkeypatch.setattr(version, "PROMPT_SOURCES", ("sandbox_agent.py",))

    before_prompt = version.prompt_hash()
    before_skills = version.skills_hash()

    (fake / "sandbox_agent.py").write_text("SYSTEM = 'v2 — now explains annualised yield'")
    version.prompt_hash.cache_clear()
    version.skills_hash.cache_clear()

    assert version.prompt_hash() != before_prompt, "a prompt edit must change prompt_hash"
    assert version.skills_hash() == before_skills, "a prompt edit must not disturb skills_hash"


def test_skills_hash_tracks_skill_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror of the above: a skill edit moves skills_hash alone."""
    fake = tmp_path / "agent"
    fake.mkdir()
    (fake / "sandbox_agent.py").write_text("SYSTEM = 'v1'")
    (fake / "skills").mkdir()
    (fake / "skills" / "analysis.py").write_text("def mean(): ...")
    monkeypatch.setattr(version, "_AGENT_DIR", fake)
    monkeypatch.setattr(version, "PROMPT_SOURCES", ("sandbox_agent.py",))

    before_prompt = version.prompt_hash()
    before_skills = version.skills_hash()

    (fake / "skills" / "analysis.py").write_text("def mean(): ...\ndef annualise(): ...")
    version.prompt_hash.cache_clear()
    version.skills_hash.cache_clear()

    assert version.skills_hash() != before_skills, "a skill edit must change skills_hash"
    assert version.prompt_hash() == before_prompt, "a skill edit must not disturb prompt_hash"


def test_missing_surface_degrades_to_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent skills directory is reported, not crashed on — the fingerprint
    is provenance, and provenance must never take the agent down."""
    fake = tmp_path / "agent"
    fake.mkdir()
    monkeypatch.setattr(version, "_AGENT_DIR", fake)
    monkeypatch.setattr(version, "PROMPT_SOURCES", ("sandbox_agent.py",))
    assert version.skills_hash() == "none"
    assert version.prompt_hash() == "none"


# ---------------------------------------------------------------------------
# The Agent SDK runtime's fingerprint (s44 M3b)
# ---------------------------------------------------------------------------


def test_build_fingerprint_dispatches_on_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_fingerprint() must route to the SDK composition only when
    AGENT_RUNTIME=agent_sdk — the champion's own call path is untouched."""
    monkeypatch.setattr(settings, "agent_runtime", "pydantic_ai")
    champion = version.build_fingerprint()
    assert champion["provider"] != version.SDK_PROVIDER

    monkeypatch.setattr(settings, "agent_runtime", "agent_sdk")
    sdk = version.build_fingerprint()
    assert sdk["provider"] == version.SDK_PROVIDER
    assert sdk["fingerprint"] == version.build_sdk_fingerprint()["fingerprint"]


def test_sdk_fingerprint_has_every_component() -> None:
    fp = version.build_sdk_fingerprint()
    for key in (
        "fingerprint",
        "provider",
        "model_id",
        "knowledge_version",
        "label",
        "runtime",
        "sdk_model",
        "claude_md_hash",
        "marts_hash",
        "schema_hash",
        "workspace_combined_hash",
        "ordinals_hash",
        "quota_hash",
        "quota_settings",
    ):
        assert fp[key], f"{key} must be populated"
    assert fp["fingerprint"].startswith("av-")
    assert fp["provider"] == "claude-agent-sdk"
    assert fp["runtime"] == "agent_sdk"
    assert json.loads(fp["quota_settings"])  # valid, non-empty JSON


def test_sdk_fingerprint_is_stable_across_calls() -> None:
    assert version.build_sdk_fingerprint() == version.build_sdk_fingerprint()


def test_sdk_fingerprint_ordinals_hash_defaults_to_the_code_seed() -> None:
    """With no explicit ordinals_hash, the sync fallback must exactly match
    ordinals.ordinals_snapshot_hash()'s own no-override case — same algorithm,
    just without the async DB read."""
    from agent import ordinals

    ordinals._OVERRIDES = {}
    ordinals._loaded_at = 0.0

    async def _no_db(*, ttl: float = ordinals._TTL_SECONDS) -> None:
        return None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ordinals, "load_overrides", _no_db)
        live = asyncio.run(ordinals.ordinals_snapshot_hash())

    fp = version.build_sdk_fingerprint()
    assert fp["ordinals_hash"] == live


def test_sdk_fingerprint_accepts_an_explicit_ordinals_hash() -> None:
    """A caller with the live (DB-override-aware) hash in hand can pass it —
    used by build_sdk_fingerprint_async so the two never disagree by algorithm,
    only by data freshness."""
    fp = version.build_sdk_fingerprint(ordinals_hash="deadbeef")
    other = version.build_sdk_fingerprint(ordinals_hash="feedface")
    assert fp["ordinals_hash"] == "deadbeef"
    assert fp["fingerprint"] != other["fingerprint"]


def test_sdk_fingerprint_moves_when_quota_settings_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quota knob is a real behaviour lever (it caps what one run can do), so
    it must move the composed fingerprint — and ONLY the quota component."""
    before = version.build_sdk_fingerprint()

    monkeypatch.setattr(settings, "max_sql_attempts", settings.max_sql_attempts + 1)
    after = version.build_sdk_fingerprint()

    assert after["quota_hash"] != before["quota_hash"]
    assert after["fingerprint"] != before["fingerprint"]
    # Nothing about the workspace/knowledge surfaces moved.
    assert after["claude_md_hash"] == before["claude_md_hash"]
    assert after["marts_hash"] == before["marts_hash"]
    assert after["schema_hash"] == before["schema_hash"]
    assert after["knowledge_version"] == before["knowledge_version"]


def test_sdk_fingerprint_moves_when_the_workspace_template_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing the CLAUDE.md template (workspace.py's one baked prompt source
    for this runtime) must move claude_md_hash and the composed fingerprint."""
    from agent import workspace as workspace_mod

    before = version.build_sdk_fingerprint()

    template = workspace_mod._TEMPLATE_PATH
    original = template.read_text(encoding="utf-8")
    try:
        template.write_text(original + "\nExtra instruction line.\n", encoding="utf-8")
        version._sdk_workspace_hashes.cache_clear()
        after = version.build_sdk_fingerprint()
    finally:
        template.write_text(original, encoding="utf-8")
        version._sdk_workspace_hashes.cache_clear()

    assert after["claude_md_hash"] != before["claude_md_hash"]
    assert after["fingerprint"] != before["fingerprint"]
    # marts/schema are untouched by a CLAUDE.md edit.
    assert after["marts_hash"] == before["marts_hash"]
    assert after["schema_hash"] == before["schema_hash"]


def test_build_sdk_fingerprint_async_uses_the_live_ordinals_value() -> None:
    """The async variant must actually call ordinals_snapshot_hash() rather
    than falling back to the seed — the whole point of the async path."""

    async def fake_hash() -> str:
        return "live-value-from-db"

    # build_sdk_fingerprint_async imports ordinals_snapshot_hash lazily from
    # .ordinals inside the function body, so it must be patched there.
    from agent import ordinals

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ordinals, "ordinals_snapshot_hash", fake_hash)
        fp = asyncio.run(version.build_sdk_fingerprint_async())

    assert fp["ordinals_hash"] == "live-value-from-db"
