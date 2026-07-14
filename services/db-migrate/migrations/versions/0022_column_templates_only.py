"""column templates only — drop the summary/insights *templates* (s14)

The pages contract keeps its semantic page *kinds* (summary / insights /
opportunities — see agent/pages.py PAGE_KINDS), but a page's *template* is now
purely a column layout. The old ``summary`` and ``insights`` template ids (which
doubled as kinds) are removed: the agent composes those kinds with the generic
``two-col`` layout instead, carrying the kind on the page dict. This re-seeds the
app.agent_config *template* rows to the three column layouts, kept in sync with
the frontend registry (report-engine/registry.ts) and agent/pages.py
TEMPLATE_IDS/TEMPLATE_COLUMNS (asserted by test_registry_sync).

Revision ID: 0022_column_templates_only
Revises: 0021_eval_runs
"""

from __future__ import annotations

import json

from alembic import op

revision = "0022_column_templates_only"
down_revision = "0021_eval_runs"
branch_labels = None
depends_on = None


# The current, canonical template registry — the drift-prevention contract test
# parses this constant, so its names + column counts must equal pages.py
# TEMPLATE_IDS / TEMPLATE_COLUMNS and the frontend registry, in order.
TEMPLATES = [
    {
        "name": "one-col",
        "title": "One column",
        "description": "Generic narrative layout: one column, objects stacked top to bottom.",
        "spec": {"columns": 1},
        "demo": {},
        "sort": 1,
    },
    {
        "name": "two-col",
        "title": "Two column",
        "description": (
            "Side-by-side layout: a narrative column beside a chart column. The agent "
            "composes summary + insights pages with this layout."
        ),
        "spec": {"columns": 2},
        "demo": {},
        "sort": 2,
    },
    {
        "name": "three-col",
        "title": "Three column",
        "description": "Three equal columns — e.g. tiles · trend · breakdown side by side.",
        "spec": {"columns": 3},
        "demo": {},
        "sort": 3,
    },
]

# What 0015 seeded, restored on downgrade so the registry round-trips.
_PRIOR = [
    {
        "name": "summary",
        "title": "Summary",
        "description": (
            "Page 1, always leads: captures the answer and builds trust — column 1 the "
            "latest number(s) + note, column 2 the trend chart (height: fill)."
        ),
        "spec": {"columns": 2},
        "demo": {
            "question": "can you show me postcode for hornsby suburb weekly rent by bedrooms",
            "example": "Hornsby (2077) 2br unit median $671/wk ▲ +6.1% YoY + trend",
        },
        "sort": 1,
    },
    {
        "name": "insights",
        "title": "Insights",
        "description": (
            "Page 2: explains the top line — column 1 per-band tiles + driver insight, "
            "column 2 the breakdown by the strongest attribute."
        ),
        "spec": {"columns": 2},
        "demo": {"example": "drivers by bedroom_band: 3br +7.3% > 2br +6.1% > 1br +3.6%"},
        "sort": 2,
    },
    {**TEMPLATES[0], "sort": 3},
    {**TEMPLATES[1], "sort": 4},
    {**TEMPLATES[2], "sort": 5},
]


def _seed(rows: list[dict]) -> None:
    op.execute("DELETE FROM app.agent_config WHERE kind = 'template'")
    for row in rows:
        op.execute(
            "INSERT INTO app.agent_config (kind, name, title, description, spec, demo, sort) "
            f"VALUES ('template', '{row['name']}', '{row['title']}', "
            f"'{row['description'].replace(chr(39), chr(39) * 2)}', "
            f"'{json.dumps(row['spec'])}'::jsonb, "
            f"'{json.dumps(row['demo']).replace(chr(39), chr(39) * 2)}'::jsonb, "
            f"{row['sort']})"
        )


def upgrade() -> None:
    _seed(TEMPLATES)
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0022_column_templates_only') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    _seed(_PRIOR)
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0022_column_templates_only'")
