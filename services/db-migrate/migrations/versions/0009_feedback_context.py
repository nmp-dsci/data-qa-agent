"""feedback context fields

Richer pinned feedback for K4. Feedback now captures sentiment, numeric
accuracy, an explicit questionable-number flag, the rendered element HTML, a
full report snapshot, and client context. This gives admins and later curator
runs enough context to understand exactly what the user was reacting to.

Revision ID: 0009_feedback_context
Revises: 0008_answer_feedback
"""

from __future__ import annotations

from alembic import op

revision = "0009_feedback_context"
down_revision = "0008_answer_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.answer_feedback ADD COLUMN accurate boolean")
    op.execute(
        "ALTER TABLE app.answer_feedback ADD COLUMN issue_flag boolean NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE app.answer_feedback ADD COLUMN target_render_html text")
    op.execute("ALTER TABLE app.answer_feedback ADD COLUMN report_snapshot jsonb")
    op.execute(
        "ALTER TABLE app.answer_feedback ADD COLUMN client_context jsonb "
        "NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0009_feedback_context') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.answer_feedback DROP COLUMN IF EXISTS client_context")
    op.execute("ALTER TABLE app.answer_feedback DROP COLUMN IF EXISTS report_snapshot")
    op.execute("ALTER TABLE app.answer_feedback DROP COLUMN IF EXISTS target_render_html")
    op.execute("ALTER TABLE app.answer_feedback DROP COLUMN IF EXISTS issue_flag")
    op.execute("ALTER TABLE app.answer_feedback DROP COLUMN IF EXISTS accurate")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0009_feedback_context'")
