"""eval_runs + eval_results — the score store (s14 E2)

Where batch grading lands. One eval_runs row per pack execution (stamped with
the agent_version under test + the judge model/prompt hash); one eval_results
row per case with the four pillar scores (g1 extract, g2 prepare, g3 present,
g4 ops) and a link back to app.query_runs for the full trace. Admin/CI-curated,
not row-level-secured — the runner writes as app_user.

Revision ID: 0021_eval_runs
Revises: 0020_agent_versions
"""

from __future__ import annotations

from alembic import op

revision = "0021_eval_runs"
down_revision = "0020_agent_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.eval_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            agent_version_id uuid REFERENCES app.agent_versions(id),
            dataset text NOT NULL DEFAULT '',
            pack text NOT NULL DEFAULT '',
            pack_version text NOT NULL DEFAULT '',
            judge_model text,
            judge_prompt_hash text,
            totals jsonb NOT NULL DEFAULT '{}'::jsonb,
            notes text
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.eval_results (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            eval_run_id uuid NOT NULL REFERENCES app.eval_runs(id) ON DELETE CASCADE,
            case_id uuid REFERENCES app.eval_cases(id) ON DELETE SET NULL,
            query_run_id uuid REFERENCES app.query_runs(id) ON DELETE SET NULL,
            tier text,
            g1 jsonb,
            g2 jsonb,
            g3 jsonb,
            g4 jsonb,
            passed boolean,
            notes text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS eval_results_run_idx ON app.eval_results (eval_run_id)")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON app.eval_runs, app.eval_results TO app_user"
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0021_eval_runs') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.eval_results")
    op.execute("DROP TABLE IF EXISTS app.eval_runs")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0021_eval_runs'")
