"""eval grader spec — how a golden should be scored (s24 M2)

``agent/eval_graders.py`` dispatches G1 on a ``kind`` (scalar / row_set /
ranked_set / series) plus the key and value columns to compare on. That is a
property of the *golden*, not of the runner: "rent trends for 2077 vs 2076" is a
series keyed on month, and only the golden's author knows that.

Stored as jsonb so the shape can grow (tolerances, expected object types for the
structural half of G3) without another migration. Empty default means "infer",
which the runner reports rather than silently guessing.

Revision ID: 0030_eval_grader_spec
Revises: 0029_eval_provenance
"""

from __future__ import annotations

from alembic import op

revision = "0030_eval_grader_spec"
down_revision = "0029_eval_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE app.eval_cases "
        "ADD COLUMN IF NOT EXISTS grader jsonb NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0030_eval_grader_spec') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.eval_cases DROP COLUMN IF EXISTS grader")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0030_eval_grader_spec'")
