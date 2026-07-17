"""Contract sync — the three template registries can never drift again.

The column-model template ids + column counts live in three places by design
(the agent may only reference what the frontend can render, and admins see the
published registry):

* ``agent/pages.py`` — TEMPLATE_IDS / TEMPLATE_COLUMNS (the validator),
* ``frontend/src/report-engine/registry.ts`` — TEMPLATES tracks (the renderer),
* migration ``0022_column_templates_only.py`` — the current app.agent_config
  seed (the published registry the Template Studio shows).

These tests parse the other two sources and assert they match pages.py, so a
new template (e.g. ``four-col``) fails CI until all three agree. Skipped when
the sibling sources aren't on disk (e.g. inside the service container).
"""

from __future__ import annotations

import ast
import re
import typing
from pathlib import Path

import pytest

from agent.pages import TEMPLATE_COLUMNS, TEMPLATE_IDS, ObjectType

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = (
    REPO_ROOT
    / "services"
    / "db-migrate"
    / "migrations"
    / "versions"
    / "0022_column_templates_only.py"
)
REGISTRY_TS = REPO_ROOT / "frontend" / "src" / "report-engine" / "registry.ts"
API_TS = REPO_ROOT / "frontend" / "src" / "lib" / "api.ts"
PAGE_LAYOUT_TSX = REPO_ROOT / "frontend" / "src" / "report-engine" / "PageLayout.tsx"

# The frontend renders these object types that the agent may NOT emit. The map
# is an Explore-tool-only object (s20 decision): Profile pages carry it, chat
# answers never do. Growing this set is a product decision, not a default.
FRONTEND_ONLY_OBJECT_TYPES = {"choropleth"}

# Renderable types that are deliberately not in the Studio playground's chart
# list: "text" is a caption, not a chart; the map is Explore-only (above).
NON_CHART_OBJECT_TYPES = {"text", "choropleth"}


def _migration_templates() -> list[dict]:
    """Extract the TEMPLATES seed constant from the migration via AST."""
    tree = ast.parse(MIGRATION.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TEMPLATES":
                    return ast.literal_eval(node.value)
    raise AssertionError("TEMPLATES constant not found in migration 0015")


@pytest.mark.skipif(not MIGRATION.exists(), reason="migration source not on disk")
def test_migration_seed_matches_template_ids() -> None:
    seed = _migration_templates()
    assert [t["name"] for t in seed] == list(TEMPLATE_IDS)
    for t in seed:
        assert t["spec"]["columns"] == TEMPLATE_COLUMNS[t["name"]], t["name"]


@pytest.mark.skipif(not REGISTRY_TS.exists(), reason="frontend source not on disk")
def test_frontend_registry_matches_template_ids() -> None:
    src = REGISTRY_TS.read_text()
    # Each TemplateDef looks like: id: "<name>", ... tracks: [ ...minmax... ].
    defs = re.findall(r'id:\s*"([a-z-]+)",[^}]*?tracks:\s*\[([^\]]*)\]', src, re.S)
    ids = [name for name, _ in defs]
    assert ids == list(TEMPLATE_IDS)
    for name, tracks in defs:
        track_count = tracks.count("minmax(")
        assert track_count == TEMPLATE_COLUMNS[name], (
            f"registry.ts {name!r} has {track_count} tracks, pages.py says {TEMPLATE_COLUMNS[name]}"
        )


# ---------------------------------------------------------------------------
# Object-type parity (s20) — the drift that let the frontend render table +
# choropleth while the agent/Studio never learned about them can't recur.
# ---------------------------------------------------------------------------


def _agent_object_types() -> set[str]:
    return set(typing.get_args(ObjectType))


def _api_ts_object_types() -> set[str]:
    """The PageObjectType union members in frontend/src/lib/api.ts."""
    src = API_TS.read_text()
    match = re.search(r"export type PageObjectType\s*=\s*((?:[^;]|\n)*?);", src)
    assert match, "PageObjectType union not found in api.ts"
    return set(re.findall(r'"([a-z-]+)"', match.group(1)))


def _object_body_cases() -> set[str]:
    """The `case "x":` arms of the ObjectBody switch in PageLayout.tsx."""
    src = PAGE_LAYOUT_TSX.read_text()
    body = src.split("export function ObjectBody", 1)[1].split("export function", 1)[0]
    return set(re.findall(r'case\s+"([a-z-]+)"', body))


def _registry_labelled_types() -> set[str]:
    """OBJECT_TYPE_LABELS keys in registry.ts — the curator-facing names."""
    src = REGISTRY_TS.read_text()
    match = re.search(r"OBJECT_TYPE_LABELS[^{]*\{(.*?)\}", src, re.S)
    assert match, "OBJECT_TYPE_LABELS not found in registry.ts"
    return set(re.findall(r'^\s*([a-z]+):\s*"', match.group(1), re.M))


def _chart_option_types() -> set[str]:
    """CHART_OPTIONS entry types in registry.ts — the Studio playground list."""
    src = REGISTRY_TS.read_text()
    match = re.search(r"CHART_OPTIONS[^\[]*\[(.*?)\n\];", src, re.S)
    assert match, "CHART_OPTIONS not found in registry.ts"
    return set(re.findall(r'type:\s*"([a-z-]+)"', match.group(1)))


@pytest.mark.skipif(not API_TS.exists(), reason="frontend source not on disk")
def test_agent_object_types_match_frontend_contract() -> None:
    """pages.py ObjectType == api.ts PageObjectType minus the explore-only map."""
    assert _agent_object_types() == _api_ts_object_types() - FRONTEND_ONLY_OBJECT_TYPES


@pytest.mark.skipif(not PAGE_LAYOUT_TSX.exists(), reason="frontend source not on disk")
def test_renderer_covers_every_object_type() -> None:
    """ObjectBody renders every declared PageObjectType (and nothing undeclared)."""
    assert _object_body_cases() == _api_ts_object_types()


@pytest.mark.skipif(not REGISTRY_TS.exists(), reason="frontend source not on disk")
def test_registry_labels_cover_every_object_type() -> None:
    assert _registry_labelled_types() == _api_ts_object_types()


@pytest.mark.skipif(not REGISTRY_TS.exists(), reason="frontend source not on disk")
def test_chart_options_enumerate_agent_charts() -> None:
    """The Studio playground (and its Playwright matrix) lists every renderable
    chart object — everything except the documented non-chart types."""
    assert _chart_option_types() == _api_ts_object_types() - NON_CHART_OBJECT_TYPES


def test_seeded_chart_registry_types_are_agent_emittable() -> None:
    """Every agent_config chart seed names an object_type the agent may emit."""
    versions = REPO_ROOT / "services" / "db-migrate" / "migrations" / "versions"
    if not versions.exists():
        pytest.skip("migration sources not on disk")
    seeded: set[str] = set()
    for path in versions.glob("*.py"):
        seeded.update(re.findall(r'"object_type":\s*"([a-z-]+)"', path.read_text()))
    assert seeded, "no chart seeds found in migrations"
    assert seeded <= _agent_object_types()
    assert "table" in seeded, "the DataTable chart seed (0027) is missing"
