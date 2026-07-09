"""template studio — column-model registry + clean cut-over (s08)

The pages contract moved from named regions to the column model: a page names a
template and fills ordered columns positionally (columns[i][j]); objects carry
type + optional semantic role + data (which may set height px/sm/md/lg/fill).

This migration:

1. re-seeds the app.agent_config *template* rows with column-model specs
   ({"columns": N}) and adds the new ``three-col`` layout — kept in sync with
   the frontend registry (report-engine/registry.ts) and agent/pages.py
   TEMPLATE_IDS/TEMPLATE_COLUMNS;
2. clears the stored report payloads in app.messages (report jsonb, which held
   the old region-shaped pages). Decision D4: the old shape is NOT supported —
   no dual-shape rendering code. Message text, generated SQL, query_runs
   metadata/trace and the feedback audit trail are all kept; reopened old chats
   simply show the text answer, and re-asking regenerates a report.

Revision ID: 0015_template_studio
Revises: 0014_agent_config
"""

from __future__ import annotations

import json

from alembic import op

revision = "0015_template_studio"
down_revision = "0014_agent_config"
branch_labels = None
depends_on = None


TEMPLATES = [
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
    {
        "name": "one-col",
        "title": "One column",
        "description": "Generic narrative layout: one column, objects stacked top to bottom.",
        "spec": {"columns": 1},
        "demo": {},
        "sort": 3,
    },
    {
        "name": "two-col",
        "title": "Two column",
        "description": "Generic side-by-side layout: narrative column beside a chart column.",
        "spec": {"columns": 2},
        "demo": {},
        "sort": 4,
    },
    {
        "name": "three-col",
        "title": "Three column",
        "description": "Three equal columns — e.g. tiles · trend · breakdown side by side.",
        "spec": {"columns": 3},
        "demo": {},
        "sort": 5,
    },
]


def upgrade() -> None:
    # 1. Re-seed the template registry with column-model specs (+ three-col).
    op.execute("DELETE FROM app.agent_config WHERE kind = 'template'")
    for row in TEMPLATES:
        op.execute(
            "INSERT INTO app.agent_config (kind, name, title, description, spec, demo, sort) "
            f"VALUES ('template', '{row['name']}', '{row['title']}', "
            f"'{row['description'].replace(chr(39), chr(39) * 2)}', "
            f"'{json.dumps(row['spec'])}'::jsonb, "
            f"'{json.dumps(row['demo']).replace(chr(39), chr(39) * 2)}'::jsonb, "
            f"{row['sort']})"
        )

    # 2. Clean cut-over (D4): drop old region-shaped report payloads. Keeps
    #    message text, SQL, query_runs metadata/trace and feedback rows.
    op.execute("UPDATE app.messages SET report = NULL WHERE report IS NOT NULL")

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0015_template_studio') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    # The cleared report payloads are gone; only the registry rows revert
    # (0014's upgrade re-creates the region-model template seed on re-run).
    op.execute("DELETE FROM app.agent_config WHERE kind = 'template'")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0015_template_studio'")
