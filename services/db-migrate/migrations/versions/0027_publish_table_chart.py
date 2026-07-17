"""agent_config — publish DataTable as a chart-registry entry

s20 chart-object unification. The frontend has rendered ``table`` page objects
since s19 (ui/charts/DataTable.tsx), but the published chart registry (kind =
'chart', seeded in 0014) never listed it, so Template Studio hid it and nothing
advertised it as an emittable object. Publish it. The map (``choropleth``)
stays deliberately unpublished — it is an Explore-tool-only object (s20
decision) and the agent never emits it.

Revision ID: 0027_publish_table_chart
Revises: 0026_query_runs_explore_source
"""

from __future__ import annotations

import json

from alembic import op

revision = "0027_publish_table_chart"
down_revision = "0026_query_runs_explore_source"
branch_labels = None
depends_on = None

TABLE_CHART = {
    "name": "DataTable",
    "title": "Data table",
    "description": "Rows and columns — plain, side-by-side comparison, or ranked with inline bars.",
    "spec": {"intent": "table", "object_type": "table"},
    "demo": {"example": "target vs comparison across every metric, with Δ and Δ% columns"},
    "sort": 5,
}


def upgrade() -> None:
    op.execute(
        "INSERT INTO app.agent_config (kind, name, title, description, spec, demo, sort) "
        f"VALUES ('chart', '{TABLE_CHART['name']}', '{TABLE_CHART['title']}', "
        f"'{TABLE_CHART['description'].replace(chr(39), chr(39) * 2)}', "
        f"'{json.dumps(TABLE_CHART['spec'])}'::jsonb, "
        f"'{json.dumps(TABLE_CHART['demo']).replace(chr(39), chr(39) * 2)}'::jsonb, "
        f"{TABLE_CHART['sort']}) "
        "ON CONFLICT (kind, name) DO NOTHING"
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0027_publish_table_chart') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.agent_config WHERE kind = 'chart' AND name = 'DataTable'")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0027_publish_table_chart'")
