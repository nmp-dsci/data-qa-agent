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
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

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


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split a `---`-delimited frontmatter block from the markdown body."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n", 1)
    if len(parts) < 2:
        return {}, text
    rest = parts[1]
    end = rest.find("\n---")
    if end == -1:
        return {}, text
    front = rest[:end]
    body = rest[end + len("\n---") :].lstrip("\n")
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
    return meta, body


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
    # version is part of the cache key so an edited tree reloads automatically.
    root = Path(dir_key)
    pages: list[Page] = []
    if not root.exists():
        return ()
    for path in sorted(root.rglob("*.md")):
        if path.name in ("INDEX.md", "README.md"):
            continue
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        name = str(meta.get("name") or path.stem)
        applies = meta.get("applies_to") or []
        pages.append(
            Page(
                name=name,
                description=str(meta.get("description") or "").strip(),
                applies_to=tuple(str(a) for a in applies) if isinstance(applies, list) else (),
                rel_path=rel,
                body=body,
                raw=raw,
            )
        )
    return tuple(pages)


def load_pages() -> tuple[Page, ...]:
    root = _knowledge_dir()
    return _load_pages_cached(str(root), knowledge_version())


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
    """Short content hash of the whole tree — recorded on every report."""
    return _version_for(str(_knowledge_dir()))


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
