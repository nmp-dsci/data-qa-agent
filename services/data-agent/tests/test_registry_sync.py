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
from pathlib import Path

import pytest

from agent.pages import TEMPLATE_COLUMNS, TEMPLATE_IDS

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
