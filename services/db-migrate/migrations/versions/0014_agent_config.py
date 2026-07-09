"""agent_config — the published composition registry (s07 report engine)

What the agent can compose, as data: the page-layout templates (with their
regions) and the visx chart functions (with their intents), demo-seeded from
the worked example (Hornsby/2077 rent by bedrooms) so the Admin → Agent-Config
tab shows the building blocks at a glance. The frontend's template registry and
the agent's pages composer both stay in sync with these ids.

Like eval_cases this is admin/CI-curated data (no per-user rows) — not
row-level-secured; the admin endpoint gates access.

Revision ID: 0014_agent_config
Revises: 0013_property_lineage_names
"""

from __future__ import annotations

import json

from alembic import op

revision = "0014_agent_config"
down_revision = "0013_property_lineage_names"
branch_labels = None
depends_on = None


TEMPLATES = [
    {
        "name": "summary",
        "title": "Summary",
        "description": (
            "Page 1, always leads: captures the answer and builds trust — the latest "
            "number in a time series with its growth rates."
        ),
        "spec": {"regions": ["hero", "chart", "note"], "layout": "one-col"},
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
            "Page 2: explains the top line — ranks the biggest driver (driver_analysis, "
            "% contribution) and shows the breakdown by the strongest attribute."
        ),
        "spec": {"regions": ["chart", "tiles", "note"], "layout": "one-col"},
        "demo": {"example": "drivers by bedroom_band: 3br +7.3% > 2br +6.1% > 1br +3.6%"},
        "sort": 2,
    },
    {
        "name": "one-col",
        "title": "One column",
        "description": "Generic narrative layout: headline, chart, insights stacked.",
        "spec": {"regions": ["headline", "chart", "insights"], "layout": "one-col"},
        "demo": {},
        "sort": 3,
    },
    {
        "name": "two-col",
        "title": "Two column",
        "description": "Generic side-by-side layout: chart beside the narrative.",
        "spec": {"regions": ["headline", "chart", "insights"], "layout": "two-col"},
        "demo": {},
        "sort": 4,
    },
]

CHARTS = [
    {
        "name": "Trend",
        "title": "Trend line",
        "description": "Time series with the house actual + rolling-average overlay.",
        "spec": {"intent": "line", "object_type": "trend"},
        "demo": {"example": "median weekly rent by month, unit 1/2/3-bed, postcode 2077"},
        "sort": 1,
    },
    {
        "name": "Breakdown",
        "title": "Breakdown bars",
        "description": "Metric by one dimension — the Insights driver view.",
        "spec": {"intent": "bar", "object_type": "breakdown"},
        "demo": {"example": "median weekly rent by bedroom_band"},
        "sort": 2,
    },
    {
        "name": "Compare",
        "title": "Grouped comparison",
        "description": "Metric by a dimension, grouped by a second series.",
        "spec": {"intent": "grouped-bar", "object_type": "compare"},
        "demo": {"example": "house vs unit median rent by bedrooms"},
        "sort": 3,
    },
    {
        "name": "KPITile",
        "title": "KPI tile",
        "description": "Latest number + secondary growth rate (+ sparkline).",
        "spec": {"intent": "kpi", "object_type": "kpi"},
        "demo": {"example": "2br unit $671/wk ▲ +6.1% YoY"},
        "sort": 4,
    },
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.agent_config (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            kind text NOT NULL CHECK (kind IN ('template', 'chart')),
            name text NOT NULL,
            title text NOT NULL,
            description text NOT NULL DEFAULT '',
            spec jsonb NOT NULL DEFAULT '{}'::jsonb,
            demo jsonb NOT NULL DEFAULT '{}'::jsonb,
            sort int NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (kind, name)
        )
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.agent_config TO app_user")

    for kind, rows in (("template", TEMPLATES), ("chart", CHARTS)):
        for row in rows:
            op.execute(
                "INSERT INTO app.agent_config (kind, name, title, description, spec, demo, sort) "
                f"VALUES ('{kind}', '{row['name']}', '{row['title']}', "
                f"'{row['description'].replace(chr(39), chr(39) * 2)}', "
                f"'{json.dumps(row['spec'])}'::jsonb, "
                f"'{json.dumps(row['demo']).replace(chr(39), chr(39) * 2)}'::jsonb, "
                f"{row['sort']})"
            )

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0014_agent_config') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.agent_config")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0014_agent_config'")
