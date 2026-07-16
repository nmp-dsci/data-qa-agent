"""backfill removed summary/insights template ids (follow-up to 0022)

0022 deleted the ``summary``/``insights`` *template* rows from app.agent_config
(templates are now a pure column layout — ``one-col``/``two-col``/``three-col``).
It did not touch persisted pages JSON that still names those retired template
ids: ``app.messages.report.pages[].template`` and
``app.eval_cases.golden_report.pages[].template``. Those rows would otherwise
render correctly only by accident, via the frontend's `TEMPLATES[id] ??
TEMPLATES['one-col']` runtime fallback (report-engine/registry.ts) — a silent
layout change on reopen rather than data that explicitly matches the new
template id space. Both retired ids used a 2-column layout, but no stored page
has been curated for a 2-column render under the new id scheme, so the correct
explicit target is ``one-col`` (matching the frontend's own fallback), not
``two-col``.

Revision ID: 0024_backfill_removed_templates
Revises: 0023_golden_objects
"""

from __future__ import annotations

from alembic import op

revision = "0024_backfill_removed_templates"
down_revision = "0023_golden_objects"
branch_labels = None
depends_on = None

# Rewrites the `pages` array of a jsonb report/golden_report column, remapping
# any page whose `template` is a retired id to `one-col`, in place. A no-op
# (WHERE clause short-circuits) when the column is null or has no such pages.
_BACKFILL_SQL = """
    UPDATE {table}
    SET {column} = jsonb_set(
        {column},
        '{{pages}}',
        (
            SELECT jsonb_agg(
                CASE
                    WHEN page ->> 'template' IN ('summary', 'insights')
                        THEN jsonb_set(page, '{{template}}', '"one-col"'::jsonb)
                    ELSE page
                END
            )
            FROM jsonb_array_elements({column} -> 'pages') AS page
        )
    )
    WHERE jsonb_typeof({column} -> 'pages') = 'array'
      AND EXISTS (
          SELECT 1 FROM jsonb_array_elements({column} -> 'pages') AS page
          WHERE page ->> 'template' IN ('summary', 'insights')
      )
"""


def upgrade() -> None:
    op.execute(_BACKFILL_SQL.format(table="app.messages", column="report"))
    op.execute(_BACKFILL_SQL.format(table="app.eval_cases", column="golden_report"))
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0024_backfill_removed_templates') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    # Irreversible by design (same precedent as 0015's clean cut-over): the
    # backfill discards which retired id ('summary' vs 'insights') a page
    # originally named, so there is nothing to restore it to.
    op.execute(
        "DELETE FROM app.schema_migrations WHERE version = '0024_backfill_removed_templates'"
    )
