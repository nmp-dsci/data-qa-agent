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
import json
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


# ---------------------------------------------------------------------------
# The Agent SDK runtime's fingerprint (s44 M3b)
# ---------------------------------------------------------------------------
#
# The champion's two behaviour-surface components (prompt_hash, skills_hash)
# have no equivalent on this runtime — there is no baked system prompt or
# skills/ dir the model reads; instead it explores a per-run *workspace*
# (workspace.py) and calls governed tools shaped by a handful of quota
# settings. So the composition swaps in this runtime's own surfaces:
# CLAUDE.md template + marts + schema content hashes, the ordinal-ordering
# state, and the quota knobs — same discipline (a component per lever), a
# different lever list.

SDK_PROVIDER = "claude-agent-sdk"


@lru_cache(maxsize=1)
def _sdk_workspace_hashes() -> dict[str, str]:
    """claude_md/marts/schema/combined content hashes for the SDK fingerprint.

    Built from a throwaway REFERENCE workspace, not a model's real per-run one:
    a real workspace's CLAUDE.md also bakes in that user's recalled memories,
    which must never move a *build* identity. ``include_insights=True`` picks
    the richer of the two pass-plan templates deterministically and
    ``memories_block=""`` keeps the render user-independent, so this is a pure
    function of the code (workspace.py, its template, schema.py, the knowledge
    tree) — cached for the process lifetime like ``prompt_hash``/``skills_hash``
    above. ``build_workspace`` touches only the filesystem (a temp dir) and
    ``schema.list_marts``/``describe_table`` (dbt-manifest or curated-catalog,
    no DB), so this is safe to compute at import-adjacent time.
    """
    import tempfile

    from .workspace import build_workspace, cleanup_workspace, workspace_manifest

    with tempfile.TemporaryDirectory(prefix="av-fingerprint-") as tmp:
        ws = build_workspace(
            "fingerprint-reference",
            "reference question for build fingerprinting",
            include_insights=True,
            memories_block="",
            base_dir=Path(tmp),
        )
        try:
            return workspace_manifest(ws)
        finally:
            cleanup_workspace(ws)


def _quota_components() -> dict[str, str]:
    """The governed-tool budgets that shape what one SDK run is allowed to do."""
    return {
        "max_sql_attempts": str(settings.max_sql_attempts),
        "sandbox_run_attempts": str(settings.sandbox_run_attempts),
        "agent_request_limit": str(settings.agent_request_limit),
        "max_knowledge_reads": str(settings.max_knowledge_reads),
    }


def _quota_hash(components: dict[str, str]) -> str:
    payload = json.dumps(components, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _seed_ordinals_hash() -> str:
    """Sync fallback for the ordinals component: the code-seed table, no DB read.

    Mirrors ``ordinals.ordinals_snapshot_hash()``'s own canonicalisation exactly
    (sorted ``(dataset, column)`` keys, tight-separator JSON), so the two agree
    whenever ``app.dataset_ordinals`` has no curator overrides — the common
    case, and exactly what that async function itself degrades to on a DB
    outage. Needed because ``build_fingerprint()`` is called synchronously from
    the ``/agent/version`` endpoint's already-running event loop, where
    ``asyncio.run()``-ing the real DB-backed hash is not safe to do inline.
    """
    from .ordinals import BAND_ORDERS

    canonical = [
        {"dataset": dataset, "column": column, "order": order}
        for (dataset, column), order in sorted(BAND_ORDERS.items())
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_sdk_fingerprint(*, ordinals_hash: str | None = None) -> dict[str, str]:
    """The Agent SDK runtime's build fingerprint.

    ``ordinals_hash`` lets a caller with an event loop in hand (mainly
    ``sdk_agent.answer_with_sdk``, which can ``await
    ordinals.ordinals_snapshot_hash()`` for the true DB-override-aware value)
    pass it in; the default computes the sync, code-seed-only fallback so this
    function stays callable from ``build_fingerprint()``'s sync context.

    Every component is returned individually (not just folded into the
    composed hash) so a caller with somewhere to put them can: see
    ``sdk_agent.py``'s per-run OTel span and ``scripts/eval_run.py``'s MLflow
    params, which is where they actually land — ``app.agent_versions`` (s24
    M1) has no jsonb/notes column to hold a components dict without a
    migration, which is out of scope here (another workstream owns
    migrations/backend-api on this build). The closest existing free-text
    field, ``label``, carries a compact one-liner instead, same spirit as the
    champion's.
    """
    model_id = settings.sdk_model
    ws_hashes = _sdk_workspace_hashes()
    k_version = knowledge_version()
    quota = _quota_components()
    q_hash = _quota_hash(quota)
    o_hash = ordinals_hash if ordinals_hash is not None else _seed_ordinals_hash()

    composed = hashlib.sha256(
        "|".join(
            [
                SDK_PROVIDER,
                model_id,
                ws_hashes["claude_md"],
                ws_hashes["marts"],
                ws_hashes["schema"],
                k_version,
                o_hash,
                q_hash,
            ]
        ).encode("utf-8")
    ).hexdigest()[:12]

    label = (
        f"{SDK_PROVIDER}/{model_id} · cmd-{ws_hashes['claude_md'][:6]} "
        f"marts-{ws_hashes['marts'][:6]} schema-{ws_hashes['schema'][:6]} "
        f"kv-{k_version[:6]} ord-{o_hash[:6]} quota-{q_hash[:6]}"
    )

    return {
        "fingerprint": f"av-{composed}",
        "provider": SDK_PROVIDER,
        "model_id": model_id,
        # The champion's two prompt/skills slots, repurposed for this
        # runtime's nearest equivalents so a table joining both runtimes'
        # agent_versions rows still has something meaningful in every column.
        "prompt_hash": f"cmd-{ws_hashes['claude_md'][:8]}",
        "skills_hash": f"ms-{ws_hashes['marts'][:4]}{ws_hashes['schema'][:4]}",
        "knowledge_version": f"kv-{k_version[:8]}",
        "image_tag": os.environ.get("IMAGE_TAG", ""),
        "git_sha": os.environ.get("GIT_SHA", ""),
        "label": label,
        # Individual components (s44 M3b) — see the docstring above for why
        # these ride here rather than as app.agent_versions columns.
        "runtime": "agent_sdk",
        "sdk_model": model_id,
        "claude_md_hash": ws_hashes["claude_md"],
        "marts_hash": ws_hashes["marts"],
        "schema_hash": ws_hashes["schema"],
        "workspace_combined_hash": ws_hashes["combined"],
        "ordinals_hash": o_hash,
        "quota_hash": q_hash,
        "quota_settings": json.dumps(quota, sort_keys=True),
    }


async def build_sdk_fingerprint_async() -> dict[str, str]:
    """``build_sdk_fingerprint`` with the LIVE ordinals snapshot (DB overrides
    included), for callers that already have an event loop and DB access —
    ``sdk_agent.answer_with_sdk``. ``ordinals_snapshot_hash()`` itself never
    raises (a DB outage degrades to the code seed), so this doesn't either.
    """
    from .ordinals import ordinals_snapshot_hash

    o_hash = await ordinals_snapshot_hash()
    return build_sdk_fingerprint(ordinals_hash=o_hash)


def build_fingerprint() -> dict[str, str]:
    """The full composed identity of this agent build.

    ``fingerprint`` is a hash of the six behaviour components, so two builds
    compare equal only when every lever matches. ``image_tag`` and ``git_sha``
    are deployment provenance — recorded, but deliberately *not* folded into the
    fingerprint, so rebuilding the same code does not invent a new agent version.

    On the ``agent_sdk`` runtime this dispatches to ``build_sdk_fingerprint()``
    instead — a process runs exactly one runtime (chosen at deploy time by
    ``AGENT_RUNTIME``), so ``/agent/version`` always describes whichever build
    is actually answering. The champion branch below is untouched.
    """
    if settings.agent_runtime == "agent_sdk":
        return build_sdk_fingerprint()
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
