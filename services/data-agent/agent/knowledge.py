"""The Insight Playbook — a versioned markdown knowledge tree the agent greps.

Pages live under ``services/data-agent/knowledge/`` (override with KNOWLEDGE_DIR).
Each page is markdown with a small YAML-ish frontmatter block:

    ---
    name: trend-charts
    description: one-line summary used in the always-loaded index
    applies_to: [trend, "over time", compare]
    ---
    # body...

Three levels of disclosure, mirroring Agent Skills:
  1. ``build_index()`` — one line per page (name · description), pinned in the
     system prompt so the agent always knows what exists.
  2. ``search_knowledge(query)`` — ripgrep-style ranked search returning page
     names + matching snippets (an agent tool).
  3. ``read_knowledge(name)`` — the full page body (an agent tool).

``knowledge_version()`` is a content hash of the whole tree — recorded on every
report so feedback can tell which knowledge produced an answer (staleness, §06).

No third-party parser is used on purpose: this module must import cleanly in the
dependency-light environments the agent runs in.

s49 (D3, W-E): the tree stays the source of truth, but a curator can now
override one page's *body* from the Architecture tab without a git commit —
``app.knowledge_pages`` (migration 0039), a ``(path -> {body, version, author,
updated_at})`` row keyed on the same ``rel_path`` a file already has. The
override layer mirrors ``agent.ordinals.load_overrides``: a module-global
cache with a short TTL, refreshed by an explicit async ``load_overrides()``
call from a caller that already has an event loop (never from this module's
own sync helpers, which must stay DB-free — see ``workspace.py``'s "never
touches the database" invariant). A DB-backed body wins over the file body
for that page (``Page.source == "db"``); frontmatter (name/description/
applies_to) always comes from the file — only the body is curator-editable.
``knowledge_version()`` folds the override snapshot into the file-tree hash,
so a curator edit moves the agent's build fingerprint exactly like an edited
file would. ``make knowledge-export`` (``scripts/knowledge_pack.py``) writes
DB overrides back into the file tree for a commit, closing the loop.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "knowledge"


def _knowledge_dir() -> Path:
    return Path(os.environ.get("KNOWLEDGE_DIR", str(_DEFAULT_DIR)))


@dataclass(frozen=True)
class Page:
    name: str
    description: str
    applies_to: tuple[str, ...]
    rel_path: str
    body: str
    raw: str = field(repr=False, default="")
    # s49 curator override metadata — "file" (the common case) or "db" (a
    # curator edit not yet exported). version/author/updated_at are 0/""/""
    # for a plain file page; frontmatter_raw is the exact "---\n...\n---\n"
    # block from the file, reused to rebuild a full page when a DB body is
    # spliced back in (workspace copies, `knowledge-export`).
    source: str = "file"
    version: int = 0
    author: str = ""
    updated_at: str = ""
    frontmatter_raw: str = field(repr=False, default="")


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str, str]:
    """Split a `---`-delimited frontmatter block from the markdown body.

    Returns ``(meta, body, frontmatter_raw)`` — ``frontmatter_raw`` is the
    reconstructed ``---\\n...\\n---\\n`` block, kept so a DB-overridden body
    can be spliced back onto the file's own frontmatter (workspace copies,
    ``knowledge-export``) without touching name/description/applies_to.
    """
    if not text.startswith("---"):
        return {}, text, ""
    parts = text.split("\n", 1)
    if len(parts) < 2:
        return {}, text, ""
    rest = parts[1]
    end = rest.find("\n---")
    if end == -1:
        return {}, text, ""
    front = rest[:end]
    body = rest[end + len("\n---") :].lstrip("\n")
    frontmatter_raw = f"---\n{front}\n---\n"
    meta: dict[str, object] = {}
    for line in front.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = _parse_list(value)
        else:
            meta[key] = value
    return meta, body, frontmatter_raw


def _parse_list(value: str) -> list[str]:
    inner = value[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    for tok in re.findall(r'"[^"]*"|[^,]+', inner):
        tok = tok.strip().strip('"').strip()
        if tok:
            items.append(tok)
    return items


@lru_cache(maxsize=1)
def _load_pages_cached(dir_key: str, version: str) -> tuple[Page, ...]:
    # version is part of the cache key so an edited tree (or a curator's DB
    # override, folded into knowledge_version() below) reloads automatically.
    root = Path(dir_key)
    pages: list[Page] = []
    if not root.exists():
        return ()
    overrides = _OVERRIDES or {}
    for path in sorted(root.rglob("*.md")):
        if path.name in ("INDEX.md", "README.md"):
            continue
        raw = path.read_text(encoding="utf-8")
        meta, body, frontmatter_raw = _parse_frontmatter(raw)
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        name = str(meta.get("name") or path.stem)
        applies = meta.get("applies_to") or []
        override = overrides.get(rel)
        source = "file"
        db_version = 0
        author = ""
        updated_at = ""
        if override is not None:
            body = str(override["body"])
            source = "db"
            db_version = int(override["version"])
            author = str(override.get("author") or "")
            updated_at = str(override.get("updated_at") or "")
        pages.append(
            Page(
                name=name,
                description=str(meta.get("description") or "").strip(),
                applies_to=tuple(str(a) for a in applies) if isinstance(applies, list) else (),
                rel_path=rel,
                body=body,
                raw=raw,
                source=source,
                version=db_version,
                author=author,
                updated_at=updated_at,
                frontmatter_raw=frontmatter_raw,
            )
        )
    # A page that exists only as a DB row (authored purely through the curator
    # UI, never exported to a file) still needs to show up everywhere a file
    # page would — synthesize one, sorted in with the rest.
    file_rels = {p.rel_path for p in pages}
    for rel, override in sorted(overrides.items()):
        if rel in file_rels:
            continue
        pages.append(
            Page(
                name=Path(rel).stem,
                description="",
                applies_to=(),
                rel_path=rel,
                body=str(override["body"]),
                raw="",
                source="db",
                version=int(override["version"]),
                author=str(override.get("author") or ""),
                updated_at=str(override.get("updated_at") or ""),
                frontmatter_raw=f"---\nname: {Path(rel).stem}\n---\n",
            )
        )
    return tuple(pages)


def load_pages() -> tuple[Page, ...]:
    root = _knowledge_dir()
    return _load_pages_cached(str(root), knowledge_version())


# ---------------------------------------------------------------------------
# Curator DB-override cache (s49, D3) — mirrors agent.ordinals.load_overrides:
# a module-global dict refreshed on a short TTL by an explicit async call from
# a caller that already has an event loop. Never queried lazily from inside a
# sync helper (build_workspace's whole point is staying DB-free).
# ---------------------------------------------------------------------------
_OVERRIDES: dict[str, dict[str, Any]] | None = None
_overrides_loaded_at: float = 0.0
_OVERRIDES_TTL = 5.0


async def load_overrides(*, ttl: float = _OVERRIDES_TTL) -> None:
    """Refresh the curator-override cache from ``app.knowledge_pages`` (best effort).

    Any failure (grants/RLS/missing table, DB unreachable) degrades silently to
    "no overrides" — the file tree still answers every page.
    """
    global _OVERRIDES, _overrides_loaded_at
    now = time.monotonic()
    if _OVERRIDES is not None and (now - _overrides_loaded_at) < ttl:
        return
    cache: dict[str, dict[str, Any]] = {}
    try:
        from sqlalchemy import text

        from .db import engine

        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT path, body, version, author, updated_at FROM app.knowledge_pages")
                )
            ).all()
        for path, body, version, author, updated_at in rows:
            cache[str(path)] = {
                "body": str(body),
                "version": int(version),
                "author": str(author or ""),
                "updated_at": updated_at.isoformat() if updated_at else "",
            }
        _OVERRIDES = cache
    except Exception:  # noqa: BLE001 — override is best-effort; the file tree is the fallback
        if _OVERRIDES is None:
            _OVERRIDES = {}
    _overrides_loaded_at = now


def _overrides_snapshot_hash() -> str:
    """Stable sha256[:12] over the current override cache state (path/version/body)."""
    overrides = _OVERRIDES or {}
    canonical = [
        {"path": path, "version": o["version"], "body": o["body"]}
        for path, o in sorted(overrides.items())
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@lru_cache(maxsize=8)
def _version_for(dir_key: str) -> str:
    root = Path(dir_key)
    if not root.exists():
        return "none"
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.md")):
        if path.name in ("INDEX.md", "README.md"):
            continue
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]


def knowledge_version() -> str:
    """Short content hash of the whole tree PLUS any curator DB overrides.

    Reads ``_OVERRIDES`` directly (never awaits ``load_overrides()`` itself —
    this must stay a sync, DB-free call so ``build_sdk_fingerprint()`` and
    every other existing caller keeps working unchanged); a caller that wants
    the override to be *fresh* awaits ``load_overrides()`` first (see
    ``workspace.workspace()``, ``main.py``'s ``/agent/version`` and
    ``/agent/architecture``). A curator edit therefore changes this value —
    and so the agent's build fingerprint — without needing a new file commit.
    """
    files_hash = _version_for(str(_knowledge_dir()))
    overrides_hash = _overrides_snapshot_hash()
    return hashlib.sha256(f"{files_hash}:{overrides_hash}".encode()).hexdigest()[:12]


def build_index() -> str:
    """The always-in-context map: one line per page, grouped by top folder."""
    pages = load_pages()
    if not pages:
        return "(knowledge tree not found)"
    groups: dict[str, list[Page]] = {}
    for p in pages:
        top = p.rel_path.split("/", 1)[0] if "/" in p.rel_path else "root"
        groups.setdefault(top, []).append(p)
    lines: list[str] = []
    for top in sorted(groups):
        lines.append(f"[{top}]")
        for p in sorted(groups[top], key=lambda x: x.name):
            lines.append(f"  {p.name} — {p.description}")
    return "\n".join(lines)


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1]


# Pages at or under this body size are cheap enough to return in full inside the
# search result, saving a whole read_knowledge round-trip (each of which re-sends
# the entire growing context to the model). Longer pages still get a snippet +
# a read_knowledge pointer so the model pulls them only when it needs them.
INLINE_CHAR_LIMIT = 1400


def search_knowledge(query: str, limit: int = 4) -> str:
    """Rank pages against a query, inlining short ones and snippeting the rest."""
    return search_knowledge_result(query, limit=limit)[0]


def search_knowledge_result(
    query: str, limit: int = 4, inline_char_limit: int = INLINE_CHAR_LIMIT
) -> tuple[str, list[str]]:
    """Ranked search text plus the names of pages returned in full (inlined).

    The caller (the agent tool) records the inlined names as already-loaded so
    the model doesn't re-fetch them with read_knowledge and the knowledge-read
    budget stays honest.
    """
    pages = load_pages()
    if not pages:
        return "No knowledge pages are available.", []
    q_tokens = set(_tokenize(query))
    q_lower = query.lower()
    scored: list[tuple[float, Page, str]] = []
    for p in pages:
        score = 0.0
        name_tokens = set(_tokenize(p.name))
        desc_tokens = set(_tokenize(p.description))
        body_lower = p.body.lower()
        score += 5.0 * len(q_tokens & name_tokens)
        score += 2.0 * len(q_tokens & desc_tokens)
        # Multi-word applies_to phrases are strong signals.
        for phrase in p.applies_to:
            if phrase.lower() in q_lower or all(t in q_tokens for t in _tokenize(phrase)):
                score += 4.0
        for tok in q_tokens:
            score += 0.5 * body_lower.count(tok)
        if score <= 0:
            continue
        snippet = _snippet(p, q_tokens)
        scored.append((score, p, snippet))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]
    if not top:
        return (
            f"No knowledge pages matched {query!r}. Use read_knowledge on an index page name.",
            [],
        )
    out = [f"Top {len(top)} knowledge pages for {query!r}:"]
    inlined: list[str] = []
    for rank, (_score, p, snippet) in enumerate(top):
        # The top hit is ALWAYS inlined in full: the model reads it anyway, and
        # excerpting it costs a whole extra read_knowledge model turn.
        if rank == 0 or len(p.body) <= inline_char_limit:
            inlined.append(p.name)
            out.append(
                f"\n### {p.name}  ({p.rel_path}) — full page inlined below "
                f"(no need to read_knowledge)\n{p.body.strip()}"
            )
        else:
            out.append(
                f"\n### {p.name}  ({p.rel_path})\n{p.description}\n> {snippet}\n"
                f"(read_knowledge('{p.name}') for the full page)"
            )
    return "\n".join(out), inlined


def _snippet(page: Page, q_tokens: set[str]) -> str:
    for line in page.body.splitlines():
        low = line.lower()
        if line.strip().startswith("#"):
            continue
        if any(tok in low for tok in q_tokens) and len(line.strip()) > 20:
            return line.strip()[:200]
    # Fall back to the first substantive line.
    for line in page.body.splitlines():
        if line.strip() and not line.strip().startswith("#"):
            return line.strip()[:200]
    return page.description


def read_knowledge(name: str) -> str:
    """Return the full body of a page by name (or rel_path)."""
    pages = load_pages()
    key = name.strip().removesuffix(".md")
    for p in pages:
        if p.name == key or p.rel_path == name or p.rel_path.removesuffix(".md") == key:
            return f"# {p.name}\n{p.body}"
    available = ", ".join(sorted(p.name for p in pages))
    return f"No page named {name!r}. Available pages: {available}"


def get_page(path: str) -> Page | None:
    """A single page by ``rel_path`` (or bare name) — the ``/agent/knowledge/{path}``
    endpoint's lookup, distinct from :func:`read_knowledge` which returns prose
    for the agent rather than a structured ``Page`` for the curator UI."""
    key = path.strip().removesuffix(".md")
    for p in load_pages():
        if p.rel_path == path or p.rel_path.removesuffix(".md") == key or p.name == key:
            return p
    return None


def list_pages_meta() -> list[dict[str, Any]]:
    """One row per page for ``GET /agent/knowledge`` — path/name/description/
    source/version/author/updated_at, no body (fetch a page for that)."""
    return [
        {
            "path": p.rel_path,
            "name": p.name,
            "description": p.description,
            "source": p.source,
            "version": p.version,
            "author": p.author,
            "updated_at": p.updated_at,
        }
        for p in load_pages()
    ]


_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def lint() -> list[str]:
    """Wiki hygiene (K6): broken cross-links, missing descriptions, duplicate names.

    Karpathy's llm-wiki keeps a lint pass so the tree doesn't rot as it grows.
    Returns a list of human-readable issues (empty == healthy).
    """
    pages = load_pages()
    issues: list[str] = []
    names = {p.name for p in pages}
    rel_stems = {p.rel_path.removesuffix(".md") for p in pages}
    seen: set[str] = set()
    for p in pages:
        if p.name in seen:
            issues.append(f"duplicate page name: {p.name}")
        seen.add(p.name)
        if not p.description:
            issues.append(f"{p.rel_path}: missing frontmatter description")
        for target in _LINK_RE.findall(p.body):
            target = target.strip()
            stem = target.split("/")[-1]
            if target not in names and target not in rel_stems and stem not in names:
                issues.append(f"{p.rel_path}: broken link [[{target}]]")
    return issues


def generate_index_file() -> str:
    """Write knowledge/INDEX.md from the live frontmatter (human artifact)."""
    root = _knowledge_dir()
    content = (
        "# Knowledge index (auto-generated — run `python -m agent.knowledge`)\n\n"
        "Do not edit by hand; regenerate from page frontmatter.\n\n```\n"
        + build_index()
        + "\n```\n"
    )
    (root / "INDEX.md").write_text(content, encoding="utf-8")
    return content


if __name__ == "__main__":  # `python -m agent.knowledge [--lint]`
    import sys

    if "--lint" in sys.argv:
        problems = lint()
        if problems:
            print("Knowledge lint found issues:")
            for issue in problems:
                print(f"  - {issue}")
            sys.exit(1)
        print(f"Knowledge tree healthy ({len(load_pages())} pages, version {knowledge_version()}).")
    else:
        print(generate_index_file())
