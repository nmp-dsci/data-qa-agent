"""The agent build fingerprint — what produced an answer (s24 M1).

An eval score is only meaningful if you know exactly which build produced it.
``app.agent_versions`` models that as a *composed* hash: provider, model, and a
content hash per behaviour surface (prompts, skills, knowledge) plus the
deployment identity (image tag, git sha). Because it is composed, comparing two
runs proves which single lever moved — the discipline that separates a real
improvement cycle from tuning noise.

The hashing pattern deliberately mirrors ``knowledge.knowledge_version()``:
sorted paths, path bytes and file bytes both folded in, truncated to 12 hex
chars so it stays readable in a CLI table.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

from .config import settings
from .knowledge import knowledge_version

_AGENT_DIR = Path(__file__).resolve().parent

# Modules whose source text defines the agent's prompts. Prompts here are built
# in code rather than stored as templates, so the source *is* the prompt: change
# any of these and the agent's instructions changed. Listed explicitly (not
# globbed) so that adding a prompt surface is a deliberate, reviewable edit.
PROMPT_SOURCES = (
    "sandbox_agent.py",
    "nl2sql.py",
    "sql_assist.py",
    "object_codegen.py",
    "skill_codegen.py",
    "titles.py",
    "report.py",
)


def _hash_files(paths: list[Path], *, root: Path) -> str:
    """Content hash over an ordered set of files, path-sensitive."""
    existing = [p for p in paths if p.is_file()]
    if not existing:
        return "none"
    h = hashlib.sha256()
    for path in sorted(existing):
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]


@lru_cache(maxsize=1)
def prompt_hash() -> str:
    """Content hash of every module that defines a system prompt."""
    return _hash_files([_AGENT_DIR / name for name in PROMPT_SOURCES], root=_AGENT_DIR)


@lru_cache(maxsize=1)
def skills_hash() -> str:
    """Content hash of the tested skill library the sandbox composes answers from."""
    skills_dir = _AGENT_DIR / "skills"
    if not skills_dir.is_dir():
        return "none"
    return _hash_files(sorted(skills_dir.rglob("*.py")), root=_AGENT_DIR)


def _active_model() -> str:
    """The model actually in use, which depends on the selected provider."""
    return settings.deepseek_model if settings.llm_provider == "deepseek" else settings.model


def build_fingerprint() -> dict[str, str]:
    """The full composed identity of this agent build.

    ``fingerprint`` is a hash of the six behaviour components, so two builds
    compare equal only when every lever matches. ``image_tag`` and ``git_sha``
    are deployment provenance — recorded, but deliberately *not* folded into the
    fingerprint, so rebuilding the same code does not invent a new agent version.
    """
    provider = settings.llm_provider
    model_id = _active_model()
    p_hash = prompt_hash()
    s_hash = skills_hash()
    k_version = knowledge_version()

    composed = hashlib.sha256(
        "|".join([provider, model_id, p_hash, s_hash, k_version]).encode("utf-8")
    ).hexdigest()[:12]

    return {
        "fingerprint": f"av-{composed}",
        "provider": provider,
        "model_id": model_id,
        "prompt_hash": f"p-{p_hash[:8]}",
        "skills_hash": f"s-{s_hash[:8]}",
        "knowledge_version": f"kv-{k_version[:8]}",
        "image_tag": os.environ.get("IMAGE_TAG", ""),
        "git_sha": os.environ.get("GIT_SHA", ""),
        # A human-readable one-liner for CLI tables and the Evaluations tab.
        "label": f"{provider}/{model_id} · p-{p_hash[:6]} · s-{s_hash[:6]} · kv-{k_version[:6]}",
    }
