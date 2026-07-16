"""eval_cases → authored golden answers (s14 E1)

Extends app.eval_cases so a golden answer can be authored end-to-end in the
Golden Answer (Builder) tab: the three executable stages — SQL extract, the
sandbox prep script, and the presentation pages (PagesEnvelope) — plus the
metadata the runner needs (dataset, tier, as_user, holdout, tags).

Feedback-promoted rows (migration 0008) and hand-authored rows now coexist via
a `source` column, so the feedback-only NOT NULLs are relaxed — authored rows
carry a question + the golden stages, not an element snapshot. A ready golden
is the 100/100 benchmark the eval runner scores the agent against.

Revision ID: 0019_eval_goldens
Revises: 0018_statement_timeouts
"""

from __future__ import annotations

from alembic import op

revision = "0019_eval_goldens"
down_revision = "0018_statement_timeouts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE app.eval_cases
            ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'feedback'
                CHECK (source IN ('feedback', 'authored')),
            ADD COLUMN IF NOT EXISTS dataset text,
            ADD COLUMN IF NOT EXISTS tier text,
            ADD COLUMN IF NOT EXISTS as_user text,
            ADD COLUMN IF NOT EXISTS tags jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS golden_sql text,
            ADD COLUMN IF NOT EXISTS golden_sandbox text,
            ADD COLUMN IF NOT EXISTS golden_data jsonb,
            ADD COLUMN IF NOT EXISTS golden_report jsonb,
            ADD COLUMN IF NOT EXISTS holdout boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS authoring_status text NOT NULL DEFAULT 'ready'
                CHECK (authoring_status IN ('draft', 'ready'))
        """
    )

    # Authored goldens don't carry a feedback element anchor — relax the
    # feedback-only NOT NULLs so hand-built rows validate.
    op.execute("ALTER TABLE app.eval_cases ALTER COLUMN target_kind DROP NOT NULL")
    op.execute("ALTER TABLE app.eval_cases ALTER COLUMN target_snapshot DROP NOT NULL")
    op.execute("ALTER TABLE app.eval_cases ALTER COLUMN expectation DROP NOT NULL")
    op.execute("ALTER TABLE app.eval_cases ALTER COLUMN knowledge_version DROP NOT NULL")

    # Speeds up the per-dataset browse in the Builder / Evaluations tabs.
    op.execute("CREATE INDEX IF NOT EXISTS eval_cases_dataset_idx ON app.eval_cases (dataset)")

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0019_eval_goldens') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app.eval_cases_dataset_idx")
    op.execute(
        """
        ALTER TABLE app.eval_cases
            DROP COLUMN IF EXISTS source,
            DROP COLUMN IF EXISTS dataset,
            DROP COLUMN IF EXISTS tier,
            DROP COLUMN IF EXISTS as_user,
            DROP COLUMN IF EXISTS tags,
            DROP COLUMN IF EXISTS golden_sql,
            DROP COLUMN IF EXISTS golden_sandbox,
            DROP COLUMN IF EXISTS golden_data,
            DROP COLUMN IF EXISTS golden_report,
            DROP COLUMN IF EXISTS holdout,
            DROP COLUMN IF EXISTS authoring_status
        """
    )
    # NOT NULLs are intentionally not re-added (authored rows may remain).
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0019_eval_goldens'")
