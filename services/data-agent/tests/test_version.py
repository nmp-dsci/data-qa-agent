"""The build fingerprint's contract (s24 M1).

The eval loop's central claim is "this improvement came from one lever". That
claim is only as good as the fingerprint: it has to change when a behaviour
surface changes, stay put when nothing does, and move *independently* per
surface so a comparison can name which lever moved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import version


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """The hashes are lru_cached, so each test must start from cold."""
    version.prompt_hash.cache_clear()
    version.skills_hash.cache_clear()


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
