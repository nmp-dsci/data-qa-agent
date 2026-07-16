"""golden_objects → named presentation objects for the Golden Sandbox (s18)

Adds ``app.eval_cases.golden_objects``: the list of *named* presentation objects
a curator builds in the Golden Sandbox (Presentation Object builder). Each entry
is ``{name, element_id, object_type, code, spec}`` — a self-contained
run_analysis snippet run against the shared golden extract, plus the structured
form ``spec`` so the object stays re-editable. These coexist with the single
``golden_sandbox`` script (the drafted first pass); the named objects are the new
add/edit/remove-by-selecting-skills/columns source of truth for a golden's
presentation datasets.

Revision ID: 0023_golden_objects
Revises: 0022_column_templates_only
"""

from __future__ import annotations

from alembic import op

revision = "0023_golden_objects"
down_revision = "0022_column_templates_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE app.eval_cases
            ADD COLUMN IF NOT EXISTS golden_objects jsonb NOT NULL DEFAULT '[]'::jsonb
        """
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0023_golden_objects') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.eval_cases DROP COLUMN IF EXISTS golden_objects")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0023_golden_objects'")
